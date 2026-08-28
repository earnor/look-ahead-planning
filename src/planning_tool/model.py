from collections import Counter
import math
import pandas as pd
from sqlalchemy import Engine, text
from typing import Optional, Dict, Any

from ortools.sat.python import cp_model


def _to_int(value) -> int:
    return int(round(float(value)))


def _cents(value) -> int:
    return int(round(float(value) * 100.0))


class PrefabScheduler:
    def __init__(self,
                 N,
                 T,
                 d,
                 E,
                 D,
                 L,
                 C_install,
                 M_machine,
                 S_site,
                 S_fac,
                 construction_day_cost,
                 transport_batch_cost,
                 hours_per_day: int = 8,
                 min_batch_size: int = 3,
                 max_batch_size: int = 5):
        """
        N: number of modules (real modules 1..N)
        T: time horizon
        d: installation duration dict{i: duration}
        E: installation precedence list of (i, j)
        D: factory production duration dict{i: duration}
        L: transport / extra lead time dict{i: lead time}
        C_install: crew number at site
        M_machine: machine number at factory
        S_site: onsite storage capacity
        S_fac: factory buffer storage capacity
        construction_day_cost: CHF per working day of project duration
        transport_batch_cost: CHF per truck batch
        hours_per_day: working hours on one calendar workday
        """
        self.N = N
        self.T = T
        self.d = d
        self.E = E
        self.D = D
        self.L = L
        self.C_install = C_install
        self.M_machine = M_machine
        self.S_site = S_site
        self.S_fac = S_fac
        self.construction_day_cost = float(construction_day_cost or 0)
        self.transport_batch_cost = float(transport_batch_cost or 0)
        self.hours_per_day = max(1, int(hours_per_day or 8))
        # A delivery carries between min and max modules; a single load may be
        # smaller because the module count rarely fills every truck.
        self.min_batch_size = min_batch_size
        self.max_batch_size = max_batch_size
        # dummies (kept for compatibility with time-window / DB helpers)
        self.dummy_start = 0
        self.dummy_end = N + 1
        self.d[self.dummy_start] = 0
        self.d[self.dummy_end] = 0

        # placeholders: model & variables
        self.m = None
        self._solver = None
        self._status = None
        self._quiet = False
        self._n_binaries = 0
        self._n_live_binaries = 0
        self._heuristic_start = None
        self._cached_solution = None
        self._true_obj = None
        self._time_limit = 120.0
        self._mip_gap = 0.15

        self.prod_start = {}
        self.arrival = {}
        self.install_start = {}
        self.finish_var = None
        self.site_wait = {}
        self.fac_wait = {}
        self.z = {}
        self.u = {}
        self.time_windows = None

        # Fixed constraints for re-optimization
        self.fixed_installation_starts = {}
        self.fixed_production_starts = {}
        self.fixed_arrival_times = {}
        self.fixed_durations = {}
        self.reoptimize_from_time = None
        self.earliest_production_starts = {}
        self.earliest_arrival_times = {}
        self.earliest_installation_starts = {}

        # preprocessing roots / leaves
        self.roots, self.leaves = self._find_roots_and_leaves()

    def set_fixed_constraints(self,
                             fixed_installation_starts: Optional[Dict[int, int]] = None,
                             fixed_production_starts: Optional[Dict[int, int]] = None,
                             fixed_arrival_times: Optional[Dict[int, int]] = None,
                             fixed_durations: Optional[Dict[int, Dict[str, float]]] = None,
                             reoptimize_from_time: Optional[int] = None,
                             earliest_production_starts: Optional[Dict[int, int]] = None,
                             earliest_arrival_times: Optional[Dict[int, int]] = None,
                             earliest_installation_starts: Optional[Dict[int, int]] = None):
        """
        Set fixed constraints for re-optimization.

        Args:
            fixed_installation_starts: {module_index: start_time}
            fixed_production_starts: {module_index: start_time}
            fixed_arrival_times: {module_index: arrival_time}
            fixed_durations: {module_index: {phase: duration}}
            reoptimize_from_time: Current time (time index) from which to re-optimize
            earliest_*: lower bounds for phases that have not started
        """
        if fixed_installation_starts:
            self.fixed_installation_starts = fixed_installation_starts.copy()
        if fixed_production_starts:
            self.fixed_production_starts = fixed_production_starts.copy()
        if fixed_arrival_times:
            self.fixed_arrival_times = fixed_arrival_times.copy()
        if fixed_durations:
            self.fixed_durations = fixed_durations.copy()
        if reoptimize_from_time is not None:
            self.reoptimize_from_time = reoptimize_from_time
        if earliest_production_starts:
            self.earliest_production_starts = earliest_production_starts.copy()
        if earliest_arrival_times:
            self.earliest_arrival_times = earliest_arrival_times.copy()
        if earliest_installation_starts:
            self.earliest_installation_starts = earliest_installation_starts.copy()

    def _find_roots_and_leaves(self):
        preds = {i: [] for i in range(1, self.N + 1)}
        succs = {i: [] for i in range(1, self.N + 1)}
        for (i, j) in self.E:
            succs[i].append(j)
            preds[j].append(i)
        roots = [i for i in range(1, self.N + 1) if len(preds[i]) == 0]
        leaves = [i for i in range(1, self.N + 1) if len(succs[i]) == 0]
        return roots, leaves

    def _topological_order(self) -> Optional[list]:
        """Modules in precedence order, or None if the precedence graph has a cycle."""
        indeg = {i: 0 for i in range(1, self.N + 1)}
        succs = {i: [] for i in range(1, self.N + 1)}
        for (i, j) in set(self.E):
            succs[i].append(j)
            indeg[j] += 1

        queue = [i for i in range(1, self.N + 1) if indeg[i] == 0]
        order = []
        while queue:
            i = queue.pop()
            order.append(i)
            for j in succs[i]:
                indeg[j] -= 1
                if indeg[j] == 0:
                    queue.append(j)

        return order if len(order) == self.N else None

    def compute_time_windows(self) -> Optional[Dict[str, Dict[int, tuple]]]:
        """
        Earliest and latest start time of every activity.

        A module cannot be installed before it has been produced and shipped, nor
        before its predecessors are done; and it cannot start so late that its
        successors no longer fit before T. Arrival and production windows follow
        from the installation window through the transport and production lead
        times. Those bounds become the domains of the CP-SAT start variables.

        Returns None when no tightening can be justified, in which case the model
        keeps the full 1..T range for every activity.
        """
        N, T = self.N, self.T
        d, D, L = self.d, self.D, self.L

        order = self._topological_order()
        if order is None:
            return None

        preds = {i: [] for i in range(1, N + 1)}
        succs = {i: [] for i in range(1, N + 1)}
        for (i, j) in self.E:
            succs[i].append(j)
            preds[j].append(i)

        # All predecessors of a module have to be installed before it starts, and
        # all its successors afterwards. With a limited number of crews that work
        # needs room in the schedule, which bounds the module far more tightly
        # than the longest chain does when the precedence graph is wide and flat.
        crew = max(1, self.C_install)
        work_before, work_after = {}, {}
        for i in order:
            done = set()
            for p in preds[i]:
                done |= work_before[p] | {p}
            work_before[i] = done
        for i in reversed(order):
            todo = set()
            for s in succs[i]:
                todo |= work_after[s] | {s}
            work_after[i] = todo

        def crew_periods(modules) -> int:
            return math.ceil(sum(d[k] for k in modules) / crew)

        tau = self.reoptimize_from_time

        # Installation, forward pass. Re-optimization pins some activities; those
        # values replace the bound so that the windows stay consistent with them.
        es_x, ls_x = {}, {}
        for i in order:
            fixed = self.fixed_installation_starts.get(i)
            if fixed is not None:
                es_x[i] = fixed
                continue
            earliest = max(1 + D[i] + L[i], 1 + crew_periods(work_before[i]))
            for p in preds[i]:
                earliest = max(earliest, es_x[p] + d[p])
            fixed_arrival = self.fixed_arrival_times.get(i)
            if fixed_arrival is not None:
                earliest = max(earliest, fixed_arrival)
            if tau is not None:
                earliest = max(earliest, tau)
            earliest_lb = self.earliest_installation_starts.get(i)
            if earliest_lb is not None:
                earliest = max(earliest, earliest_lb)
            es_x[i] = earliest

        # Installation, backward pass. A leaf must also finish before the dummy
        # end activity, which itself cannot start later than T.
        for i in reversed(order):
            fixed = self.fixed_installation_starts.get(i)
            if fixed is not None:
                ls_x[i] = fixed
                continue
            latest = T - d[i] + 1 - crew_periods(work_after[i])
            if succs[i]:
                latest = min(latest, min(ls_x[j] - d[i] for j in succs[i]))
            else:
                latest = min(latest, T - d[i])
            ls_x[i] = latest

        # Arrival and production follow from the installation window.
        es_p, ls_p, es_q, ls_q = {}, {}, {}, {}
        for i in range(1, N + 1):
            fixed_arrival = self.fixed_arrival_times.get(i)
            fixed_prod = self.fixed_production_starts.get(i)

            if fixed_arrival is not None:
                es_p[i] = ls_p[i] = fixed_arrival
            else:
                es_p[i] = 1 + D[i] + L[i]
                if fixed_prod is not None:
                    es_p[i] = max(es_p[i], fixed_prod + D[i] + L[i])
                if tau is not None:
                    es_p[i] = max(es_p[i], tau)
                earliest_arr = self.earliest_arrival_times.get(i)
                if earliest_arr is not None:
                    es_p[i] = max(es_p[i], earliest_arr)
                ls_p[i] = ls_x[i]

            if fixed_prod is not None:
                es_q[i] = ls_q[i] = fixed_prod
            else:
                es_q[i] = 1 if tau is None else max(1, tau)
                earliest_prod = self.earliest_production_starts.get(i)
                if earliest_prod is not None:
                    es_q[i] = max(es_q[i], earliest_prod)
                ls_q[i] = ls_p[i] - D[i] - L[i]

        windows = {
            "install": {i: (es_x[i], ls_x[i]) for i in range(1, N + 1)},
            "arrival": {i: (es_p[i], ls_p[i]) for i in range(1, N + 1)},
            "production": {i: (es_q[i], ls_q[i]) for i in range(1, N + 1)},
        }

        # An empty window means the horizon or the pinned values cannot hold this
        # schedule. Tightening would then turn a merely tight model into an
        # infeasible one, so drop the preprocessing and let the solver decide.
        for kind, per_module in windows.items():
            for i, (lo, hi) in per_module.items():
                if lo > hi or hi < 1 or lo > T:
                    print(f"[TimeWindows] {kind} window for module {i} is empty "
                          f"({lo}..{hi}, T={T}); skipping the preprocessing.")
                    return None

        dummy_end_earliest = max(es_x[i] + d[i] for i in self.leaves) if self.leaves else 1

        # Every installation has to fit between the first possible start and the
        # end of the project, and the crews can only do so much per period. This
        # bounds the project end far below what the precedence chains alone give.
        if es_x:
            total_install = sum(d[i] for i in range(1, N + 1))
            dummy_end_earliest = max(
                dummy_end_earliest,
                min(es_x.values()) + math.ceil(total_install / crew),
            )

        windows["dummy_end"] = (max(1, min(dummy_end_earliest, T)), T)
        return windows

    def _pinned_time(self, kind: str, i: int):
        if kind == "production":
            return self.fixed_production_starts.get(i)
        if kind == "arrival":
            return self.fixed_arrival_times.get(i)
        if kind == "install":
            return self.fixed_installation_starts.get(i)
        return None

    def _domain(self, kind: str, i: int) -> tuple:
        """Inclusive (lo, hi) for a start variable, clamped to 1..T."""
        T = self.T
        pinned = self._pinned_time(kind, i)
        if pinned is not None:
            t = max(1, min(int(pinned), T))
            return t, t
        if self.time_windows is None:
            tau = self.reoptimize_from_time
            lo = 1 if tau is None else max(1, tau)
            return lo, T
        lo, hi = self.time_windows[kind][i]
        return max(1, min(int(lo), T)), max(1, min(int(hi), T))

    def _true_objective(self, finish: int, n_trucks: int) -> float:
        hours = max(1, int(self.hours_per_day))
        days = int(math.ceil(max(0, int(finish)) / hours))
        return (
            self.construction_day_cost * days
            + self.transport_batch_cost * n_trucks
        )

    def _status_name(self, status) -> str:
        if status == cp_model.OPTIMAL:
            return "optimal"
        if status == cp_model.FEASIBLE:
            return "feasible"
        if status == cp_model.INFEASIBLE:
            return "infeasible"
        if status == cp_model.MODEL_INVALID:
            return "infeasible"
        return "unknown"

    def _configure_solver(self, solver, time_limit=600.0, mip_gap=0.01):
        """
        Search for better incumbents within the time limit. Gap 1% is a stop
        criterion; the time limit is the usual exit.
        """
        solver.parameters.max_time_in_seconds = float(time_limit)
        solver.parameters.relative_gap_limit = float(mip_gap)
        solver.parameters.log_search_progress = not self._quiet
        solver.parameters.repair_hint = True

    def build_model(self):
        m = cp_model.CpModel()
        m.SetName("prefab_cp_sat")
        self._n_binaries = 0
        self._n_live_binaries = 0
        self._cached_solution = None
        self._true_obj = None

        N, T = self.N, self.T
        d = {i: _to_int(self.d[i]) for i in range(1, N + 1)}
        D = {i: _to_int(self.D[i]) for i in range(1, N + 1)}
        L = {i: _to_int(self.L[i]) for i in range(1, N + 1)}
        C_install = _to_int(self.C_install)
        M_machine = _to_int(self.M_machine)
        S_site = _to_int(self.S_site)
        S_fac = _to_int(self.S_fac)

        windows = self.compute_time_windows()
        self.time_windows = windows

        def new_bool(name: str):
            self._n_binaries += 1
            self._n_live_binaries += 1
            return m.new_bool_var(name)

        # ---- start variables (domains from time windows) ----
        prod_start, arrival, install_start = {}, {}, {}
        for i in range(1, N + 1):
            q_lo, q_hi = self._domain("production", i)
            p_lo, p_hi = self._domain("arrival", i)
            x_lo, x_hi = self._domain("install", i)
            prod_start[i] = m.new_int_var(q_lo, q_hi, f"prod_{i}")
            arrival[i] = m.new_int_var(p_lo, p_hi, f"arr_{i}")
            install_start[i] = m.new_int_var(x_lo, x_hi, f"inst_{i}")

        if windows is None:
            finish_lo, finish_hi = 1, T
        else:
            finish_lo, finish_hi = windows["dummy_end"]
            finish_lo = max(1, min(int(finish_lo), T))
            finish_hi = max(1, min(int(finish_hi), T))
        finish = m.new_int_var(finish_lo, finish_hi, "finish")

        # ---- intervals ----
        # Production occupies [prod_start, prod_start + D); installation
        # occupies [install_start, install_start + d). Site storage occupies
        # [arrival, install_start); factory storage occupies
        # [prod_start + D, arrival - L).
        prod_intervals, install_intervals = [], []
        site_intervals, factory_intervals = [], []
        site_wait, fac_wait = {}, {}
        for i in range(1, N + 1):
            prod_intervals.append(
                m.new_fixed_size_interval_var(prod_start[i], D[i], f"prod_iv_{i}")
            )
            install_intervals.append(
                m.new_fixed_size_interval_var(install_start[i], d[i], f"inst_iv_{i}")
            )

            max_site_wait = T
            site_wait[i] = m.new_int_var(0, max_site_wait, f"site_wait_{i}")
            site_intervals.append(
                m.new_interval_var(
                    arrival[i], site_wait[i], install_start[i], f"site_iv_{i}"
                )
            )

            max_fac_wait = T
            fac_wait[i] = m.new_int_var(0, max_fac_wait, f"fac_wait_{i}")
            factory_intervals.append(
                m.new_interval_var(
                    prod_start[i] + D[i],
                    fac_wait[i],
                    arrival[i] - L[i],
                    f"fac_iv_{i}",
                )
            )

        ones = [1] * N
        m.add_cumulative(prod_intervals, ones, M_machine)
        m.add_cumulative(install_intervals, ones, C_install)
        m.add_cumulative(site_intervals, ones, S_site)
        m.add_cumulative(factory_intervals, ones, S_fac)

        # ---- timing and precedence ----
        for i in range(1, N + 1):
            m.add(prod_start[i] + D[i] + L[i] <= arrival[i])
            m.add(arrival[i] <= install_start[i])

        for (i, j) in self.E:
            m.add(install_start[j] >= install_start[i] + d[i])

        m.add_max_equality(finish, [install_start[i] + d[i] for i in range(1, N + 1)])

        # ---- re-optimization pins and persistent earliest bounds ----
        for i, t in self.fixed_installation_starts.items():
            if 1 <= i <= N:
                m.add(install_start[i] == _to_int(t))
        for i, t in self.fixed_production_starts.items():
            if 1 <= i <= N:
                m.add(prod_start[i] == _to_int(t))
        for i, t in self.fixed_arrival_times.items():
            if 1 <= i <= N:
                m.add(arrival[i] == _to_int(t))
        for i, t in self.earliest_production_starts.items():
            if 1 <= i <= N and i not in self.fixed_production_starts:
                m.add(prod_start[i] >= _to_int(t))
        for i, t in self.earliest_arrival_times.items():
            if 1 <= i <= N and i not in self.fixed_arrival_times:
                m.add(arrival[i] >= _to_int(t))
        for i, t in self.earliest_installation_starts.items():
            if 1 <= i <= N and i not in self.fixed_installation_starts:
                m.add(install_start[i] >= _to_int(t))

        # ---- truck channeling ----
        # arrival[i] == t <=> arrival_at[i][t]. Load at t is the number of
        # modules that share that arrival slot; z[t] marks a truck, u[t] the
        # single allowed partial load (size below min_batch_size).
        if windows is None:
            delivery_lo, delivery_hi = 1, T
        else:
            delivery_lo = min(lo for lo, _ in windows["arrival"].values())
            delivery_hi = max(hi for _, hi in windows["arrival"].values())
            delivery_lo = max(1, min(int(delivery_lo), T))
            delivery_hi = max(1, min(int(delivery_hi), T))

        arrival_at = {i: {} for i in range(1, N + 1)}
        for i in range(1, N + 1):
            p_lo, p_hi = self._domain("arrival", i)
            bools = []
            for t in range(p_lo, p_hi + 1):
                b = new_bool(f"arr_eq_{i}_{t}")
                arrival_at[i][t] = b
                bools.append(b)
            m.add_map_domain(arrival[i], bools, offset=p_lo)

        z, u = {}, {}
        for t in range(delivery_lo, delivery_hi + 1):
            load_terms = [arrival_at[i][t] for i in range(1, N + 1) if t in arrival_at[i]]
            if not load_terms:
                continue
            z[t] = new_bool(f"z_{t}")
            u[t] = new_bool(f"u_{t}")
            load = sum(load_terms)
            m.add(load <= self.max_batch_size * z[t])
            m.add(load >= z[t])
            m.add(load >= self.min_batch_size * z[t] - self.min_batch_size * u[t])
            m.add(u[t] <= z[t])

        if u:
            m.add(sum(u[t] for t in u) <= 1)

        # ---- objective: construction-day cost × working days + batch cost × trucks ----
        n_trucks = sum(z[t] for t in z) if z else 0
        hours = max(1, int(self.hours_per_day))
        max_days = max(1, (T + hours - 1) // hours)
        finish_days = m.new_int_var(1, max_days, "finish_days")
        m.add(finish_days * hours >= finish)
        cents_day = _cents(self.construction_day_cost)
        cents_truck = _cents(self.transport_batch_cost)
        if cents_day == cents_truck == 0:
            cents_day = 1
        m.minimize(cents_day * finish_days + cents_truck * n_trucks)

        self.m = m
        self.prod_start = prod_start
        self.arrival = arrival
        self.install_start = install_start
        self.finish_var = finish
        self.site_wait = site_wait
        self.fac_wait = fac_wait
        self.z = z
        self.u = u
        return m

    def hide_output(self, quiet: bool = True):
        self._quiet = bool(quiet)

    def binary_counts(self):
        """(declared binaries, binaries that time windows did not drop)."""
        return self._n_binaries, self._n_live_binaries

    def n_constraints(self) -> int:
        if self.m is None:
            return 0
        return len(self.m.proto.constraints)

    def n_sols(self) -> int:
        if self._status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return 1
        return 0

    def solver_status(self) -> Optional[str]:
        if self._status is None:
            return None
        return self._status_name(self._status)

    def obj_value(self) -> float:
        if self._true_obj is not None:
            return self._true_obj
        if self._solver is None:
            return 0.0
        return float(self._solver.objective_value) / 100.0

    def dual_bound(self) -> float:
        if self._solver is None:
            return 0.0
        return float(self._solver.best_objective_bound) / 100.0

    def mip_gap(self) -> float:
        if self._solver is None:
            return 0.0
        obj = float(self._solver.objective_value)
        if abs(obj) < 1e-9:
            return 0.0
        return abs(obj - float(self._solver.best_objective_bound)) / abs(obj)

    def load_heuristic_start(self, heur) -> bool:
        """
        Register the constructive schedule as a CP-SAT solution hint.

        The horizon T still comes from this schedule. Hinting the three start
        times (and the truck indicators) gives CP-SAT an incumbent immediately.
        """
        if self.m is None or heur is None:
            return False
        self._heuristic_start = heur
        return self._apply_heuristic_hints(heur)

    def _apply_heuristic_hints(self, heur) -> bool:
        if self.m is None or heur is None:
            return False
        self.m.clear_hints()
        N, T = self.N, self.T
        d = {i: _to_int(self.d[i]) for i in range(1, N + 1)}
        D = {i: _to_int(self.D[i]) for i in range(1, N + 1)}
        L = {i: _to_int(self.L[i]) for i in range(1, N + 1)}
        hinted = 0
        arrival_counts = Counter()
        try:
            for i in range(1, N + 1):
                xs = int(heur.install_start[i])
                qs = int(heur.prod_start[i])
                ps = int(heur.arrival_time[i])
                if not (1 <= xs <= T and 1 <= qs <= T and 1 <= ps <= T):
                    raise ValueError(f"heuristic times for module {i} outside 1..{T}")
                self.m.add_hint(self.install_start[i], xs)
                self.m.add_hint(self.prod_start[i], qs)
                self.m.add_hint(self.arrival[i], ps)
                self.m.add_hint(self.site_wait[i], max(0, xs - ps))
                self.m.add_hint(self.fac_wait[i], max(0, (ps - L[i]) - (qs + D[i])))
                arrival_counts[ps] += 1
                hinted += 1

            dummy_end_t = max(int(heur.install_start[i]) + d[i] for i in range(1, N + 1))
            dummy_end_t = min(max(int(dummy_end_t), 1), T)
            self.m.add_hint(self.finish_var, dummy_end_t)

            for t, zt in self.z.items():
                self.m.add_hint(zt, 1 if arrival_counts[t] else 0)
            partial = [t for t, c in arrival_counts.items() if 0 < c < self.min_batch_size]
            for t, ut in self.u.items():
                self.m.add_hint(ut, 1 if (partial and t == partial[0]) else 0)
        except Exception as exc:
            print(f"[CP-SAT hint] Could not assemble the heuristic solution: {exc}")
            self.m.clear_hints()
            return False

        print(f"[CP-SAT hint] Heuristic schedule hinted for {hinted} modules.")
        return True

    def solve(self, time_limit=None, mip_gap=None, heuristic=None):
        if self.m is None:
            self.build_model()

        if time_limit is not None:
            self._time_limit = time_limit
        if mip_gap is not None:
            self._mip_gap = mip_gap

        if heuristic is not None:
            self.load_heuristic_start(heuristic)

        solver = cp_model.CpSolver()
        self._configure_solver(solver, self._time_limit, self._mip_gap)
        self._solver = solver
        self._cached_solution = None
        self._true_obj = None

        status = solver.solve(self.m)
        self._status = status
        name = self._status_name(status)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            sol = self.get_solution_dict()
            if sol is not None:
                print(
                    f"[CP-SAT] {name} in {solver.wall_time:.1f}s: "
                    f"finish={sol['project_finish_time']}, "
                    f"trucks={len(sol['order_times'])}, "
                    f"obj={sol['objective']:.4f}, gap={self.mip_gap():.2%}"
                )
        else:
            print(f"[CP-SAT] {name} in {solver.wall_time:.1f}s (no solution).")

        if not self._quiet:
            print(solver.response_stats())
        return name

    def get_solution_dict(self) -> Optional[Dict[str, Any]]:
        """
        Extract solution values from the solved model.
        Returns a dictionary with all solution data, or None if model not solved.
        """
        if self._cached_solution is not None:
            return self._cached_solution
        if self.m is None or self._solver is None:
            return None
        if self._status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        solver = self._solver
        N, T = self.N, self.T
        D = {i: _to_int(self.D[i]) for i in range(1, N + 1)}
        L = {i: _to_int(self.L[i]) for i in range(1, N + 1)}

        installation_start = {i: int(solver.value(self.install_start[i])) for i in range(1, N + 1)}
        arrival_time = {i: int(solver.value(self.arrival[i])) for i in range(1, N + 1)}
        production_start = {i: int(solver.value(self.prod_start[i])) for i in range(1, N + 1)}
        order_times = sorted(t for t, zt in self.z.items() if solver.value(zt) > 0.5)
        finish = int(solver.value(self.finish_var))

        factory_inventory = {}
        factory = 0
        for s in range(2, T + 1):
            finished = sum(
                1 for i in range(1, N + 1) if production_start[i] + D[i] == s
            )
            shipped = sum(
                1 for i in range(1, N + 1) if arrival_time[i] - L[i] == s
            )
            factory = factory + finished - shipped
            if factory > 1e-6:
                factory_inventory[s] = factory

        site_inventory = {}
        for i in range(1, N + 1):
            for t in range(arrival_time[i], installation_start[i]):
                site_inventory[(i, t)] = 1

        site_total = sum(
            max(0, installation_start[i] - arrival_time[i]) for i in range(1, N + 1)
        )
        factory_total = sum(
            max(0, (arrival_time[i] - L[i]) - (production_start[i] + D[i]))
            for i in range(1, N + 1)
        )
        true_obj = self._true_objective(finish, len(order_times))
        self._true_obj = true_obj

        solution = {
            "objective": true_obj,
            "status": self._status_name(self._status),
            "installation_start": installation_start,
            "arrival_time": arrival_time,
            "production_start": production_start,
            "order_times": order_times,
            "factory_inventory": factory_inventory,
            "site_inventory": site_inventory,
            "project_finish_time": finish,
        }
        self._cached_solution = solution
        return solution

    def save_results_to_db(self,
                          engine: Engine,
                          project_id: int,
                          module_id_mapping: Optional[Dict[int, str]] = None,
                          version_id: Optional[int] = None) -> bool:
        """
        Save optimization results to the database.

        For each project, this creates/maintains:
        - raw_schedule_{project_id}: Input data from user's file (read-only, managed by datamanager)
        - solution_schedule_{project_id}: Optimization solution results (this method creates/updates)
        - optimization_summary_{project_id}: Project-level summary statistics
        - factory_inventory_{project_id}: Factory inventory levels over time
        - site_inventory_{project_id}: Site inventory levels over time

        Args:
            engine: SQLAlchemy Engine instance
            project_id: Project ID to save results for
            module_id_mapping: Optional mapping from module index (1..N) to module ID string.
                             If None, uses module index as ID.
            version_id: Optional version ID for version management. If None, uses latest version.

        Returns:
            True if successful, False otherwise
        """
        solution = self.get_solution_dict()
        if solution is None:
            return False

        try:
            # Get table names from datamanager if available
            try:
                from .datamanager import ScheduleDataManager
                solution_table = ScheduleDataManager.solution_table_name(project_id)
                summary_table = ScheduleDataManager.summary_table_name(project_id)
                factory_inv_table = ScheduleDataManager.factory_inventory_table_name(project_id)
                site_inv_table = ScheduleDataManager.site_inventory_table_name(project_id)
            except ImportError:
                # Fallback if datamanager is not available
                solution_table = f'solution_schedule_{project_id}'
                summary_table = f'optimization_summary_{project_id}'
                factory_inv_table = f'factory_inventory_{project_id}'
                site_inv_table = f'site_inventory_{project_id}'

            # Create results DataFrame
            results_data = []
            N = self.N

            for i in range(1, N + 1):
                module_id = module_id_mapping[i] if module_id_mapping else f"Module_{i}"
                install_start = solution['installation_start'].get(i)
                arrival_time = solution['arrival_time'].get(i)
                prod_start = solution['production_start'].get(i)
                prod_duration = self.D.get(i, 0)
                prod_finish = prod_start + prod_duration -1 if prod_start else None
                factory_wait_start = prod_finish + 1
                onsite_wait_start = arrival_time
                onsite_wait_duration = install_start - onsite_wait_start  # Duration is the difference between time indices
                transport_duration = self.L.get(i, 0)
                transport_start = arrival_time - transport_duration if arrival_time else None
                factory_wait_duration = transport_start - factory_wait_start  # Duration is the difference between time indices
                install_duration = self.d.get(i, 0)
                install_finish = install_start + install_duration -1 if install_start else None

                # Debug: Print when factory_wait_duration is 1 to understand why
                print(f"Module {module_id}, prod_start: {prod_start}, prod_finish: {prod_finish}, factory_wait_start: {factory_wait_start}, factory_wait_duration: {factory_wait_duration}")

                results_data.append({
                    'Module_ID': module_id,
                    'Module_Index': i,
                    'Installation_Start': install_start,
                    'Installation_Finish': install_finish,
                    'Installation_Duration': install_duration,
                    'Arrival_Time': arrival_time,
                    'Production_Start': prod_start,
                    'Production_Duration': self.D.get(i, 0),
                    'Factory_Wait_Start': factory_wait_start,
                    'Factory_Wait_Duration': factory_wait_duration,
                    'Onsite_Wait_Start': onsite_wait_start,
                    'Onsite_Wait_Duration': onsite_wait_duration,
                    'Transport_Start': transport_start,
                    'Transport_Duration': transport_duration,
                    'version_id': version_id
                })

            results_df = pd.DataFrame(results_data)

            # Ensure solution table has version_id column and delete old data for this version if table exists
            with engine.begin() as conn:
                from sqlalchemy import inspect
                inspector = inspect(engine)
                if solution_table in inspector.get_table_names():
                    # Table exists: check if version_id column exists and add if needed
                    columns = [col['name'] for col in inspector.get_columns(solution_table)]
                    if 'version_id' not in columns:
                        conn.exec_driver_sql(f'ALTER TABLE "{solution_table}" ADD COLUMN version_id INTEGER')

                    # Delete old data for this version before appending new data
                    if version_id is not None:
                        # Delete old data for this specific version
                        delete_query = text(f'DELETE FROM "{solution_table}" WHERE version_id = :version_id')
                        conn.execute(delete_query, {"version_id": version_id})
                    else:
                        # For backward compatibility: delete NULL version_id data if version_id is None
                        # (This should not happen in new architecture, but kept for safety)
                        delete_query = text(f'DELETE FROM "{solution_table}" WHERE version_id IS NULL')
                        conn.execute(delete_query)
                # If table doesn't exist, it will be created by to_sql with append mode

            # Append new data (table will be created automatically if it doesn't exist)
            results_df.to_sql(
                solution_table,
                engine,
                if_exists='append',
                index=False,
                method='multi',
                chunksize=1000
            )

            # Also create a summary table with project-level results
            # ---- 版本累计策略 ----
            # 为 optimization_summary_{project_id} 增加 version_id 字段，
            # 不再整表 replace，而是：
            #   - 按 version_id 维度累积多条记录（多版本并存）
            #   - 如果同一 version_id 重新求解，则先删掉该 version_id 的旧记录，再追加新记录
            summary_data = [{
                'project_id': project_id,
                'version_id': version_id,
                'objective_value': solution['objective'],
                'status': solution['status'],
                'project_finish_time': solution['project_finish_time'],
                'num_orders': len(solution['order_times']),
                'order_times': ','.join(map(str, sorted(solution['order_times'])))
            }]

            summary_df = pd.DataFrame(summary_data)

            # 确保 summary 表存在 version_id 列，并按版本做“先删再插”
            with engine.begin() as conn:
                from sqlalchemy import inspect as _inspect_summary
                inspector_summary = _inspect_summary(engine)
                if summary_table in inspector_summary.get_table_names():
                    # 表已存在：如果没有 version_id 列则新增
                    summary_columns = [col['name'] for col in inspector_summary.get_columns(summary_table)]
                    if 'version_id' not in summary_columns:
                        conn.exec_driver_sql(f'ALTER TABLE "{summary_table}" ADD COLUMN version_id INTEGER')
                    # 如果当前有 version_id（新架构下应总是如此），对同一版本先删除旧记录
                    if version_id is not None:
                        delete_summary = text(f'DELETE FROM "{summary_table}" WHERE version_id = :version_id')
                        conn.execute(delete_summary, {"version_id": version_id})
                    else:
                        # 兼容旧数据：没有传 version_id 时，清理 version_id IS NULL 的记录
                        delete_summary = text(f'DELETE FROM "{summary_table}" WHERE version_id IS NULL')
                        conn.execute(delete_summary)
                # 如果表不存在，则交给 to_sql 使用 append 自动建表

            # 采用 append 方式写入，实现“版本累计”
            summary_df.to_sql(
                summary_table,
                engine,
                if_exists='append',
                index=False
            )

            # Create factory inventory table
            if solution['factory_inventory']:
                factory_inv_data = [
                    {'time': t, 'inventory_level': inv}
                    for t, inv in sorted(solution['factory_inventory'].items())
                ]
                factory_inv_df = pd.DataFrame(factory_inv_data)
                factory_inv_df.to_sql(
                    factory_inv_table,
                    engine,
                    if_exists='replace',
                    index=False
                )

            # Create site inventory table
            if solution['site_inventory']:
                site_inv_data = [
                    {'module_index': i, 'time': t, 'inventory_level': inv}
                    for (i, t), inv in sorted(solution['site_inventory'].items())
                ]
                site_inv_df = pd.DataFrame(site_inv_data)
                site_inv_df.to_sql(
                    site_inv_table,
                    engine,
                    if_exists='replace',
                    index=False
                )
            return True

        except Exception as e:
            print(f"Error saving results to database: {e}")
            return False
