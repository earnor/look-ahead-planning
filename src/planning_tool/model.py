from collections import Counter
from pyscipopt import (
    Model,
    quicksum,
    Heur,
    SCIP_PARAMEMPHASIS,
    SCIP_PARAMSETTING,
    SCIP_RESULT,
    SCIP_HEURTIMING,
)
import math
import pandas as pd
from sqlalchemy import Engine, text
from typing import Optional, Dict, Any

def _add_cons(m, expr, name: str):
    """Named constraint helper so the MIP body stays solver-agnostic."""
    return m.addCons(expr, name=name)


class _ScheduleStartHeur(Heur):
    """Submits the constructive schedule in original variable space (avoids presolve aggregations)."""

    def heurexec(self, heurtiming, nodeinfeasible):
        if getattr(self, "_injected", False):
            return {"result": SCIP_RESULT.DIDNOTRUN}
        self._injected = True
        ok = self.scheduler._inject_heuristic_sol(self)
        return {"result": SCIP_RESULT.FOUNDSOL if ok else SCIP_RESULT.DIDNOTFIND}


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
                 w_duration,
                 w_transport,
                 w_site_storage,
                 w_factory_storage,
                 reference,
                 min_batch_size: int = 3,
                 max_batch_size: int = 5):
        """
        in English
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
        w_*: priority of each objective term, meant to add up to 1
        reference: values each term is divided by. Duration and transport keep
            their own scale. The two storage terms share one denominator (the
            heuristic's total waiting time), so the weights compare a factory
            module-period with a site module-period instead of being decided
            by whichever side happened to be empty in the heuristic.
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
        self.w_duration = w_duration
        self.w_transport = w_transport
        self.w_site_storage = w_site_storage
        self.w_factory_storage = w_factory_storage
        raw = {k: float(v) for k, v in reference.items()}
        # A zero divisor would blow up the term it scales. The two storage
        # columns are kept separately for diagnostics; the MIP uses their sum
        # so a JIT heuristic (factory wait = 0) cannot make factory storage
        # look a hundred times more expensive than site storage.
        self.reference = {
            "ref_duration": max(1.0, raw["ref_duration"]),
            "ref_transport": max(1.0, raw["ref_transport"]),
            "ref_site_storage": raw.get("ref_site_storage", 0.0),
            "ref_factory_storage": raw.get("ref_factory_storage", 0.0),
            "ref_storage": max(
                1.0,
                raw.get("ref_site_storage", 0.0) + raw.get("ref_factory_storage", 0.0),
            ),
        }
        # A delivery carries between min and max modules; a single load may be
        # smaller because the module count rarely fills every truck.
        self.min_batch_size = min_batch_size
        self.max_batch_size = max_batch_size
        # dummies
        self.dummy_start = 0
        self.dummy_end = N + 1
        self.d[self.dummy_start] = 0
        self.d[self.dummy_end] = 0

        # placeholders: model & variables
        self.m = None
        self.x = {}
        self.y = {}
        self.p = {}
        self.I = {}
        self.q = {}
        self.z = {}
        self.F = {}
        self.u = {}
        self.time_windows = None
        self._n_binaries = 0
        self._n_live_binaries = 0
        self._heuristic_start = None
        
        # Fixed constraints for re-optimization
        self.fixed_installation_starts = {}
        self.fixed_production_starts = {}
        self.fixed_arrival_times = {} 
        self.fixed_durations = {} # any problem here?
        self.reoptimize_from_time = None  # Current time (time index) from which to re-optimize
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
        times. Variables outside their window are created with an upper bound of
        zero, so presolve drops them instead of leaving them to branch on.

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
        # bounds the project end far below what the precedence chains alone give,
        # which is what the relaxation otherwise exploits to look cheap.
        if es_x:
            total_install = sum(d[i] for i in range(1, N + 1))
            dummy_end_earliest = max(
                dummy_end_earliest,
                min(es_x.values()) + math.ceil(total_install / crew),
            )

        windows["dummy_end"] = (max(1, min(dummy_end_earliest, T)), T)
        return windows

    def _add_bin(self, m, name: str, ub: float):
        """Binary variable; ub=0 is how time-window tightening removes a start time."""
        ubf = float(ub)
        self._n_binaries += 1
        if ubf >= 0.5:
            self._n_live_binaries += 1
        return m.addVar(vtype="B", lb=0.0, ub=ubf, name=name)

    def _configure_solver(self, m, time_limit=600.0, mip_gap=0.01):
        """
        Search for better incumbents: feasibility emphasis and aggressive
        primal heuristics. Cuts stay light so the time is not spent only at
        the root. Gap 1% is a stop criterion; the time limit is the usual exit.
        """
        m.setEmphasis(SCIP_PARAMEMPHASIS.FEASIBILITY, True)
        m.setPresolve(SCIP_PARAMSETTING.FAST)
        m.setSeparating(SCIP_PARAMSETTING.FAST)
        m.setHeuristics(SCIP_PARAMSETTING.AGGRESSIVE)
        m.setParam("limits/time", time_limit)
        m.setParam("limits/gap", mip_gap)
        m.setParam("separating/maxroundsroot", 2)
        m.setParam("separating/maxstallroundsroot", 1)
        m.setParam("branching/relpscost/sbiterofs", 0)
        m.setParam("branching/relpscost/sbiterquot", 0.0)

    def build_model(self):
        m = Model("prefab_with_factory_buffer")
        self._configure_solver(m)
        self._n_binaries = 0
        self._n_live_binaries = 0

        N, T = self.N, self.T
        d = self.d
        D = self.D
        L = self.L
        dummy_start = self.dummy_start
        dummy_end = self.dummy_end

        # ============ 3. variables ============
        # Activities can only run inside their time window, so every variable
        # outside it is fixed to zero and disappears in presolve.
        windows = self.compute_time_windows()
        self.time_windows = windows

        def bound(kind: str, i: int, t: int) -> float:
            if windows is None:
                return 1.0
            lo, hi = windows[kind][i]
            return 1.0 if lo <= t <= hi else 0.0

        start_time_dummy = 1 if self.reoptimize_from_time is None else max(1, self.reoptimize_from_time)

        # x[i,t] start installation (including dummy)
        x = {}
        for i in range(0, N + 2):
            for t in range(1, T + 1):
                if i == dummy_start:
                    ub = 1.0 if t == start_time_dummy else 0.0
                elif i == dummy_end:
                    ub = 1.0 if windows is None or windows["dummy_end"][0] <= t <= windows["dummy_end"][1] else 0.0
                else:
                    ub = bound("install", i, t)
                x[i, t] = self._add_bin(m, f"x_{i}_{t}", ub)

        # y[i,t] installing (only real activities)
        y = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if windows is None:
                    ub = 1.0
                else:
                    lo, hi = windows["install"][i]
                    ub = 1.0 if lo <= t <= hi + d[i] - 1 else 0.0
                y[i, t] = self._add_bin(m, f"y_{i}_{t}", ub)

        # p[i,t] arrival at site
        p = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                p[i, t] = self._add_bin(m, f"p_{i}_{t}", bound("arrival", i, t))

        # site inventory: a module only occupies the yard between its earliest
        # arrival and its latest installation start
        I = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if windows is None:
                    ub = None
                else:
                    ub = 1.0 if windows["arrival"][i][0] <= t < windows["install"][i][1] else 0.0
                I[i, t] = m.addVar(vtype="C", lb=0.0, ub=ub, name=f"I_{i}_{t}")

        # factory production start
        q = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                q[i, t] = self._add_bin(m, f"q_{i}_{t}", bound("production", i, t))

        # order per time (batch): no delivery can happen outside the periods in
        # which some module is able to arrive
        if windows is None:
            delivery_lo, delivery_hi = 1, T
        else:
            delivery_lo = min(lo for lo, _ in windows["arrival"].values())
            delivery_hi = max(hi for _, hi in windows["arrival"].values())

        z = {}
        for t in range(1, T + 1):
            ub = 1.0 if delivery_lo <= t <= delivery_hi else 0.0
            z[t] = self._add_bin(m, f"z_{t}", ub)

        # factory inventory
        F = {}
        for s in range(1, T + 1):
            F[s] = m.addVar(vtype="C", lb=0.0, name=f"F_{s}")

        # ============ 4. constraints ============

        # (1) dummy start fixed at time 1 (or reoptimize_from_time (current_time) if set)
        start_time = 1
        if self.reoptimize_from_time is not None:
            start_time = max(1, self.reoptimize_from_time)
        
        _add_cons(m, x[dummy_start, start_time] == 1, "dummy_start_fix")
        for t in range(1, T + 1):
            if t != start_time:
                _add_cons(m, x[dummy_start, t] == 0, f"dummy_start_zero_{t}")

        # (2) each real activity starts once
        for i in range(1, N + 1):
            _add_cons(m, quicksum(x[i, t] for t in range(1, T + 1)) == 1,
                        f"start_once_{i}")
        
        # (2a) Fixed installation starts (for re-optimization)
        # Note: Since we have sum(x[i, t]) = 1, fixing x[i, fixed_start] = 1 
        # automatically forces all other x[i, t] = 0
        for i, fixed_start in self.fixed_installation_starts.items():
            if 1 <= i <= N and 1 <= fixed_start <= T:
                _add_cons(m, x[i, fixed_start] == 1, f"fixed_install_start_{i}")
        
        # (2b) Fixed production starts
        # Note: Since we have sum(q[i, t]) = 1, fixing q[i, fixed_start] = 1
        # automatically forces all other q[i, t] = 0
        for i, fixed_start in self.fixed_production_starts.items():
            if 1 <= i <= N and 1 <= fixed_start <= T:
                _add_cons(m, q[i, fixed_start] == 1, f"fixed_prod_start_{i}")
        
        # (2c) Fixed arrival times
        # Note: Since we have sum(p[i, t]) = 1, fixing p[i, fixed_arrival] = 1
        # automatically forces all other p[i, t] = 0
        for i, fixed_arrival in self.fixed_arrival_times.items():
            if 1 <= i <= N and 1 <= fixed_arrival <= T:
                _add_cons(m, p[i, fixed_arrival] == 1, f"fixed_arrival_{i}")
        
        # (2d) Fixed durations
        # Note: Duration extensions are handled by updating self.D, self.d, self.L dictionaries
        # before creating PrefabScheduler. The fixed_durations here are mainly for documentation
        # and ensuring consistency. The actual durations used in constraints come from D, d, L.
        for i, phase_durations in self.fixed_durations.items():
            if 1 <= i <= N:
                if 'FABRICATION' in phase_durations:
                    # Duration is stored in D[i] and used in constraints
                    # No additional constraint needed - D[i] already reflects the delayed duration
                    pass
                if 'TRANSPORT' in phase_durations:
                    # Transport duration is in L[i] and used in constraints
                    pass
                if 'INSTALLATION' in phase_durations:
                    # Installation duration is in d[i] and used in constraints
                    pass

        # (3) dummy end starts once
        _add_cons(m, quicksum(x[dummy_end, t] for t in range(1, T + 1)) == 1,
                    "dummy_end_once")

        # (4) precedence between real activities
        for (i, j) in self.E:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            start_j = quicksum(t * x[j, t] for t in range(1, T + 1))
            _add_cons(m, start_i + d[i] <= start_j, f"prec_{i}_{j}")

        # (5) roots after dummy start
        for i in self.roots:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            _add_cons(m, 1 <= start_i, f"root_after_dummy_{i}")

        # (6) leaves before dummy end
        for i in self.leaves:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            end_d = quicksum(t * x[dummy_end, t] for t in range(1, T + 1))
            _add_cons(m, start_i + d[i] <= end_d, f"leaf_before_dummy_end_{i}")

        # (7) installation state
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                tau_min = max(1, t - d[i] + 1)
                _add_cons(m, 
                    y[i, t] == quicksum(x[i, tau] for tau in range(tau_min, t + 1)),
                    f"in_install_{i}_{t}"
                )

        # (8) installation crew capacity
        for t in range(1, T + 1):
            _add_cons(m, quicksum(y[i, t] for i in range(1, N + 1)) <= self.C_install,
                        f"crew_{t}")

        # (9) arrival once
        for i in range(1, N + 1):
            _add_cons(m, quicksum(p[i, t] for t in range(1, T + 1)) == 1,
                        f"arrive_once_{i}")

        # (10) arrival no later than installation start
        for i in range(1, N + 1):
            arr = quicksum(t * p[i, t] for t in range(1, T + 1))
            sta = quicksum(t * x[i, t] for t in range(1, T + 1))
            _add_cons(m, arr <= sta, f"arrive_before_install_{i}")

        # (11) site inventory balance
        for i in range(1, N + 1):
            # t = 1
            _add_cons(m, I[i, 1] == p[i, 1] - x[i, 1], f"site_inv_init_{i}")
            # t >= 2
            for t in range(2, T + 1):
                _add_cons(m, 
                    I[i, t] == I[i, t - 1] + p[i, t] - x[i, t],
                    f"site_inv_bal_{i}_{t}"
                )

        # (12) site warehouse capacity
        for t in range(1, T + 1):
            _add_cons(m, 
                quicksum(I[i, t] for i in range(1, N + 1)) <= self.S_site,
                f"site_cap_{t}"
            )

        # (13) production -> arrival timing
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                latest_prod = t - D[i] - L[i]
                if latest_prod >= 1:
                    _add_cons(m, 
                        p[i, t] <= quicksum(q[i, tau] for tau in range(1, latest_prod + 1)),
                        f"prod_to_arrive_{i}_{t}"
                    )
                else:
                    # cannot arrive this early
                    _add_cons(m, p[i, t] == 0, f"too_early_arrive_{i}_{t}")

        # (14) factory machine capacity
        for t in range(1, T + 1):
            _add_cons(m, 
                quicksum(
                    q[i, tau]
                    for i in range(1, N + 1)
                    for tau in range(max(1, t - D[i] + 1), t + 1)
                ) <= self.M_machine,
                f"machine_cap_{t}"
            )

        # (15) order bundling
        for t in range(1, T + 1):
            for i in range(1, N + 1):
                _add_cons(m, p[i, t] <= z[t], f"link_order_{i}_{t}")

        # (15b) truck load size: a delivery carries between min_batch_size and
        # max_batch_size modules. u[t] marks the single partial load that the
        # project is allowed to ship, since N rarely fills every truck exactly.
        u = {}
        for t in range(1, T + 1):
            ub = 1.0 if delivery_lo <= t <= delivery_hi else 0.0
            u[t] = self._add_bin(m, f"u_{t}", ub)

        _add_cons(m, quicksum(u[t] for t in range(1, T + 1)) <= 1, "single_partial_load")
        for t in range(1, T + 1):
            load = quicksum(p[i, t] for i in range(1, N + 1))
            _add_cons(m, u[t] <= z[t], f"partial_load_needs_delivery_{t}")
            _add_cons(m, load >= z[t], f"delivery_not_empty_{t}")
            _add_cons(m, load <= self.max_batch_size * z[t], f"load_max_{t}")
            _add_cons(m, 
                load >= self.min_batch_size * z[t] - self.min_batch_size * u[t],
                f"load_min_{t}"
            )

        # (16) factory inventory: F1 = 0
        _add_cons(m, F[1] == 0, "factory_init")

        # (17) factory inventory recursion
        for s in range(2, T + 1):
            finished_here = quicksum(
                q[i, s - D[i]] for i in range(1, N + 1) if s - D[i] >= 1
            )
            shipped_here = quicksum(
                p[i, s + L[i]] for i in range(1, N + 1) if s + L[i] <= T
            )
            _add_cons(m, 
                F[s] == F[s - 1] + finished_here - shipped_here,
                f"factory_inv_bal_{s}"
            )

        # (18) factory buffer capacity
        for s in range(1, T + 1):
            _add_cons(m, F[s] <= self.S_fac, f"factory_cap_{s}")

        # ============ 5. objective ============
        # Duration and transport are different units, so each keeps its own
        # reference. Site and factory storage are both module-periods and share
        # one, so the weights 0.4 / 0.1 mean a factory wait is a quarter as
        # costly as the same wait on site.
        finish_time = quicksum(t * x[dummy_end, t] for t in range(1, T + 1))
        deliveries = quicksum(z[t] for t in range(1, T + 1))
        factory_storage = quicksum(F[s] for s in range(1, T + 1))
        site_storage = quicksum(I[i, t] for i in range(1, N + 1) for t in range(1, T + 1))

        ref = self.reference
        m.setObjective(
            self.w_duration * finish_time / ref["ref_duration"]
            + self.w_transport * deliveries / ref["ref_transport"]
            + self.w_site_storage * site_storage / ref["ref_storage"]
            + self.w_factory_storage * factory_storage / ref["ref_storage"],
            "minimize",
        )

        # 保存对象
        self.m = m
        self.x = x
        self.y = y
        self.p = p
        self.I = I
        self.q = q
        self.z = z
        self.F = F
        self.u = u

        return m

    def hide_output(self, quiet: bool = True):
        if self.m is not None:
            self.m.hideOutput(quiet)

    def binary_counts(self):
        """(declared binaries, binaries that time windows did not fix to zero)."""
        return self._n_binaries, self._n_live_binaries

    def n_constraints(self) -> int:
        return 0 if self.m is None else self.m.getNConss()

    def n_sols(self) -> int:
        return 0 if self.m is None else self.m.getNSols()

    def solver_status(self) -> Optional[str]:
        return None if self.m is None else self.m.getStatus()

    def obj_value(self) -> float:
        return self.m.getObjVal()

    def dual_bound(self) -> float:
        return self.m.getDualbound()

    def mip_gap(self) -> float:
        return self.m.getGap()

    def val(self, var) -> float:
        return self.m.getVal(var)

    def load_heuristic_start(self, heur) -> bool:
        """
        Register the constructive schedule as a SCIP primal heuristic.

        The horizon T still comes from this schedule. Submitting it as well
        gives SCIP an incumbent immediately; generic MIP heuristics rarely
        construct a feasible time-indexed plan on their own.
        """
        if self.m is None or heur is None:
            return False
        self._heuristic_start = heur
        plugin = _ScheduleStartHeur()
        plugin.scheduler = self
        self.m.includeHeur(
            plugin,
            "heurschedule",
            "inject constructive look-ahead schedule",
            "S",
            priority=1000000,
            freq=1,
            timingmask=SCIP_HEURTIMING.BEFORENODE,
        )
        return True

    def _inject_heuristic_sol(self, plugin) -> bool:
        """Fill original-space values so presolve aggregations cannot block the start."""
        heur = self._heuristic_start
        if self.m is None or heur is None:
            return False
        m = self.m
        N, T = self.N, self.T
        d, D, L = self.d, self.D, self.L
        sol = m.createOrigSol(plugin)

        start_time = 1 if self.reoptimize_from_time is None else max(1, self.reoptimize_from_time)
        m.setSolVal(sol, self.x[self.dummy_start, start_time], 1.0)

        dummy_end_t = max(int(heur.install_start[i]) + d[i] for i in range(1, N + 1))
        if self.time_windows is not None:
            lo, hi = self.time_windows["dummy_end"]
            dummy_end_t = min(max(dummy_end_t, lo), hi)
        dummy_end_t = min(max(int(dummy_end_t), 1), T)
        m.setSolVal(sol, self.x[self.dummy_end, dummy_end_t], 1.0)

        arrival_counts = Counter()
        try:
            for i in range(1, N + 1):
                xs = int(heur.install_start[i])
                qs = int(heur.prod_start[i])
                ps = int(heur.arrival_time[i])
                if not (1 <= xs <= T and 1 <= qs <= T and 1 <= ps <= T):
                    raise ValueError(f"heuristic times for module {i} outside 1..{T}")
                m.setSolVal(sol, self.x[i, xs], 1.0)
                m.setSolVal(sol, self.q[i, qs], 1.0)
                m.setSolVal(sol, self.p[i, ps], 1.0)
                arrival_counts[ps] += 1
                last_install = min(T, xs + d[i] - 1)
                for t in range(xs, last_install + 1):
                    m.setSolVal(sol, self.y[i, t], 1.0)
                for t in range(1, T + 1):
                    m.setSolVal(sol, self.I[i, t], 1.0 if ps <= t < xs else 0.0)

            for t in arrival_counts:
                m.setSolVal(sol, self.z[t], 1.0)
            partial = [t for t, c in arrival_counts.items() if c < self.min_batch_size]
            if partial:
                m.setSolVal(sol, self.u[partial[0]], 1.0)

            m.setSolVal(sol, self.F[1], 0.0)
            factory = 0.0
            for s in range(2, T + 1):
                finished = sum(
                    1 for i in range(1, N + 1)
                    if s - D[i] >= 1 and int(heur.prod_start[i]) == s - D[i]
                )
                shipped = sum(
                    1 for i in range(1, N + 1)
                    if s + L[i] <= T and int(heur.arrival_time[i]) == s + L[i]
                )
                factory = factory + finished - shipped
                m.setSolVal(sol, self.F[s], factory)
        except Exception as exc:
            print(f"[MIP start] Could not assemble the heuristic solution: {exc}")
            return False

        accepted = m.trySol(
            sol,
            printreason=True,
            completely=False,
            checkbounds=True,
            checkintegrality=True,
            checklprows=True,
        )
        if accepted:
            print("[MIP start] Heuristic schedule accepted as a primal solution.")
        else:
            print("[MIP start] Heuristic schedule was rejected; SCIP will search from scratch.")
        return bool(accepted)

    def solve(self, time_limit=None, mip_gap=None, heuristic=None):
        if self.m is None:
            self.build_model()

        if heuristic is not None:
            self.load_heuristic_start(heuristic)

        if time_limit is not None:
            self.m.setParam("limits/time", time_limit)
        if mip_gap is not None:
            self.m.setParam("limits/gap", mip_gap)

        self.m.optimize()
        return self.m.getStatus()

    def get_solution_dict(self) -> Optional[Dict[str, Any]]:
        """
        Extract solution values from the solved model.
        Returns a dictionary with all solution data, or None if model not solved.
        """
        if self.m is None:
            return None
        if self.m.getNSols() == 0:
            return None
        status = self.m.getStatus() 
        solution = {
            'objective': self.m.getObjVal(),
            'status': status,
            'installation_start': {},  # {module_id: time}
            'arrival_time': {},        # {module_id: time}
            'production_start': {},    # {module_id: time}
            'order_times': [],         # list of times when orders are placed
            'factory_inventory': {},  # {time: inventory_level}
            'site_inventory': {},     # {(module_id, time): inventory_level}
            'project_finish_time': None
        }
        
        N, T = self.N, self.T
        x, p, q, F, z = self.x, self.p, self.q, self.F, self.z
        dummy_end = self.dummy_end
        getVal = self.m.getVal
        
        # Extract installation start times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if getVal(x[i, t]) > 0.5:
                    solution['installation_start'][i] = t
                    break
        
        # Extract arrival times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if getVal(p[i, t]) > 0.5:
                    solution['arrival_time'][i] = t
                    break
        
        # Extract production start times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if getVal(q[i, t]) > 0.5:
                    solution['production_start'][i] = t
                    break
        
        # Extract order times
        for t in range(1, T + 1):
            if getVal(z[t]) > 0.5:
                solution['order_times'].append(t)
        
        # Extract factory inventory
        for s in range(1, T + 1):
            val = getVal(F[s])
            if val > 1e-6:
                solution['factory_inventory'][s] = val
        
        # Extract site inventory
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                val = getVal(self.I[i, t])
                if val > 1e-6:
                    solution['site_inventory'][(i, t)] = val
        
        # Extract project finish time
        for t in range(1, T + 1):
            if getVal(x[dummy_end, t]) > 0.5:
                solution['project_finish_time'] = t
                break
        
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
