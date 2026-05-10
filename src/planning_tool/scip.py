from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Set, Optional, Any

from pyscipopt import Model, quicksum


@dataclass
class ReoptState:
    """
    State used for rolling re-optimization.

    current_time:
        The time from which re-optimization starts.

    completed_installations:
        Modules already fully installed before current_time.

    site_inventory_modules:
        Modules already arrived at site but not yet installed.

    factory_inventory_modules:
        Modules already produced and stored in the factory.

    ongoing_installations:
        Modules currently being installed. 输入到里面的数据具体是什么
        Format:
            {
                i: {
                    "start_time": ...,
                    "remaining_duration": ...
                }
            }
        or:
            {
                i: {
                    "finish_time": ...
                }
            }

    ongoing_productions:
        Modules currently being produced.
        Format is the same as ongoing_installations.
    """
    current_time: int
    completed_installations: Set[int] = field(default_factory=set)
    site_inventory_modules: Set[int] = field(default_factory=set)
    factory_inventory_modules: Set[int] = field(default_factory=set)
    ongoing_installations: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    ongoing_productions: Dict[int, Dict[str, Any]] = field(default_factory=dict)


def estimate_time_horizon(
    start_date: date,
    end_date: date,
    hours_per_day: float = 8.0,
    safety_factor: float = 1.0,
) -> int:
    """
    Estimate time horizon T in working hours from project start/end dates.
    """
    if end_date <= start_date:
        total_days = 1
    else:
        total_days = (end_date - start_date).days + 1

    raw_T = total_days * hours_per_day * safety_factor
    T = int(raw_T)
    if raw_T > T:
        T += 1

    return max(1, T)


class PrefabScheduler:
    def __init__(
        self,
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
        OC,
        C_I,
        C_F,
        C_O,
    ):
        """
        Parameters
        ----------
        N:
            Number of real modules. Real modules are indexed as 1, ..., N.

        T:
            Time horizon. The model uses discrete time periods.

        d:
            Installation duration dict: {i: duration}.

        E:
            Installation precedence list: [(i, j), ...],
            meaning module i must be installed before module j.

        D:
            Factory production duration dict: {i: duration}.

        L:
            Transport / lead time dict: {i: lead time}.

        C_install:
            Number of installation crews at site.

        M_machine:
            Number of factory machines.

        S_site:
            On-site storage capacity.

        S_fac:
            Factory buffer storage capacity.

        OC:
            Cost per arrival batch.

        C_I:
            Indirect cost per unit time.

        C_F:
            Factory inventory cost per unit time per module.

        C_O:
            On-site inventory cost per unit time per module.
        """

        self.N = int(N)
        self.T = int(T)

        self.d = d.copy()
        self.E = list(E)
        self.D = D.copy()
        self.L = L.copy()

        self.C_install = C_install
        self.M_machine = M_machine
        self.S_site = S_site
        self.S_fac = S_fac

        self.OC = OC
        self.C_I = C_I
        self.C_F = C_F
        self.C_O = C_O

        # SCIP model
        self.m = None

        # Decision variables
        self.x = {}
        self.y = {}
        self.p = {}
        self.I = {}
        self.q = {}
        self.z = {}
        self.F = {}
        self.Cmax = None # finish time

        # Re-optimization state
        self.reopt_state: Optional[ReoptState] = None

        # Module sets used in re-optimization
        self.installation_decision_modules = set()
        self.arrival_decision_modules = set()
        self.production_decision_modules = set()

        self.ongoing_install_finish = {}
        self.ongoing_prod_finish = {}

        # Optional preprocessing information
        self.roots, self.leaves = self._find_roots_and_leaves()


    # ------------------------------------------------------------------
    # Basic helper functions
    # ------------------------------------------------------------------

    def _find_roots_and_leaves(self):
        preds = {i: [] for i in range(1, self.N + 1)}
        succs = {i: [] for i in range(1, self.N + 1)}

        for i, j in self.E:
            succs[i].append(j)
            preds[j].append(i)

        roots = [i for i in range(1, self.N + 1) if len(preds[i]) == 0]
        leaves = [i for i in range(1, self.N + 1) if len(succs[i]) == 0]

        return roots, leaves

    def _safe_set_param(self, model, name, value):
        """
        SCIP parameter names may differ across versions.
        This helper avoids crashing if a parameter is unavailable.
        """
        try:
            model.setParam(name, value)
        except Exception:
            pass

    def _apply_default_scip_parameters(self, model):
        """
        Default SCIP parameters.

        These can be overwritten later in solve().
        """
        self._safe_set_param(model, "limits/time", 120.0) #SCIP的参数设置是包含slash的
        self._safe_set_param(model, "limits/gap", 0.2)
        self._safe_set_param(model, "randomization/randomseedshift", 0)

        # Use a positive number of threads.
        self._safe_set_param(model, "parallel/maxnthreads", 8)

        # Optional heuristic emphasis.
        self._safe_set_param(model, "heuristics/emphasis", "aggressive")

        # Optional weakening of separation.
        self._safe_set_param(model, "separating/maxrounds", 0)
        self._safe_set_param(model, "separating/maxroundsroot", 0)

    def _get_remaining_finish_time(self, current_time, info):
        """
        Convert ongoing task information into an absolute finish time.

        The state may provide either:
        - remaining_duration
        - finish_time

        Example
        -------
        If remaining_duration = 3 and current_time = 10,
        the task occupies periods 10, 11, 12 and finishes at 13.
        """

        if "finish_time" in info:
            return int(info["finish_time"])

        if "remaining_duration" in info:
            return int(current_time + info["remaining_duration"])

        raise ValueError(
            "Ongoing task info must contain either 'remaining_duration' or 'finish_time'."
        )

    def _selected_time_expr(self, var_dict, i, time_set):
        """
        Return sum_t t * var[i,t].
        This is used for start time or arrival time expressions.
        """
        return quicksum(t * var_dict[i, t] for t in time_set)

    # ------------------------------------------------------------------
    # State setting and validation
    # ------------------------------------------------------------------

    def set_reoptimization_state(self, state: Dict):
        """
        Set the current state for rolling re-optimization.

        Expected state format
        ---------------------
        state = {
            "current_time": 10,

            "completed_installations": {1, 2},

            "site_inventory_modules": {4, 5},

            "factory_inventory_modules": {6},

            "ongoing_installations": {
                3: {
                    "start_time": 9,
                    "remaining_duration": 2
                }
            },

            "ongoing_productions": {
                7: {
                    "start_time": 8,
                    "remaining_duration": 3
                }
            }
        }
        """

        if "current_time" not in state:
            raise ValueError("state must contain 'current_time'.")

        self.reopt_state = ReoptState(
            current_time=int(state["current_time"]),
            completed_installations=set(state.get("completed_installations", set())),
            site_inventory_modules=set(state.get("site_inventory_modules", set())),
            factory_inventory_modules=set(state.get("factory_inventory_modules", set())),
            ongoing_installations=state.get("ongoing_installations", {}).copy(),
            ongoing_productions=state.get("ongoing_productions", {}).copy(),
        )

        # Existing model becomes outdated after state update.
        self.m = None

    def _classify_modules_for_reopt(self):
        """
        Classify modules into decision sets.

        installation_decision_modules:
            Modules that still need a new installation start decision.

        arrival_decision_modules:
            Modules that still need an arrival decision.
            Modules already in site inventory do not need arrival decision.

        production_decision_modules:
            Modules that still need a production start decision.
            Modules already in factory inventory or ongoing production do not need production decision.
        """

        state = self.reopt_state
        all_modules = set(range(1, self.N + 1))

        completed = state.completed_installations
        site_inv = state.site_inventory_modules
        factory_inv = state.factory_inventory_modules
        ongoing_install = set(state.ongoing_installations.keys())
        ongoing_prod = set(state.ongoing_productions.keys())

        installation_decision_modules = (
            all_modules
            - completed
            - ongoing_install
        )

        arrival_decision_modules = (
            installation_decision_modules
            - site_inv
        )

        production_decision_modules = (
            arrival_decision_modules
            - factory_inv
            - ongoing_prod
        )

        self.installation_decision_modules = installation_decision_modules
        self.arrival_decision_modules = arrival_decision_modules
        self.production_decision_modules = production_decision_modules

    def _build_ongoing_finish_times(self):
        state = self.reopt_state
        current_time = state.current_time

        self.ongoing_install_finish = {
            i: self._get_remaining_finish_time(current_time, info)
            for i, info in state.ongoing_installations.items()
        }

        self.ongoing_prod_finish = {
            i: self._get_remaining_finish_time(current_time, info)
            for i, info in state.ongoing_productions.items()
        }
        # 得到正在进行的活动的完成时间

    # ------------------------------------------------------------------
    # Main model construction
    # ------------------------------------------------------------------

    def build_reoptimization_model(self):
        """
        Build the rolling re-optimization SCIP model.

        Time convention
        ---------------
        I[i,t]:
            On-site inventory of module i at the end of period t.

        F[t]:
            Aggregate factory inventory at the end of period t.

        p[i,t]:
            Module i arrives at site at time t.

        q[i,t]:
            Production of module i starts at time t.

        x[i,t]:
            Installation of module i starts at time t.

        z[t]:
            Arrival batch indicator at time t.
            If any module arrives at site at t, z[t] = 1.

        Cmax:
            Project finish time.
        """
        self._classify_modules_for_reopt()
        self._build_ongoing_finish_times()

        state = self.reopt_state
        current_time = state.current_time

        N, T = self.N, self.T
        d, D, L = self.d, self.D, self.L

        time_set = range(current_time, T + 1)

        completed_installations = state.completed_installations
        site_inventory_modules = state.site_inventory_modules
        factory_inventory_modules = state.factory_inventory_modules

        ongoing_installation_modules = set(state.ongoing_installations.keys())
        ongoing_production_modules = set(state.ongoing_productions.keys())

        installation_decision_modules = self.installation_decision_modules
        arrival_decision_modules = self.arrival_decision_modules
        production_decision_modules = self.production_decision_modules

        ongoing_install_finish = self.ongoing_install_finish
        ongoing_prod_finish = self.ongoing_prod_finish #就是预测完成的时间

        m = Model("prefab_reoptimization")
        self._apply_default_scip_parameters(m) #对于SCIP的模型进行一些参数设置

        # ==============================================================
        # Variables
        # ==============================================================

        x = {}
        for i in installation_decision_modules:
            for t in time_set:
                x[i, t] = m.addVar(vtype="B", name=f"x_{i}_{t}")

        y = {}
        for i in installation_decision_modules:
            for t in time_set:
                y[i, t] = m.addVar(vtype="B", name=f"y_{i}_{t}")

        p = {}
        for i in arrival_decision_modules:
            for t in time_set:
                p[i, t] = m.addVar(vtype="B", name=f"p_{i}_{t}")

        I = {}
        for i in installation_decision_modules:
            for t in time_set:
                I[i, t] = m.addVar(vtype="C", lb=0.0, name=f"I_{i}_{t}")

        q = {}
        for i in production_decision_modules:
            for t in time_set:
                q[i, t] = m.addVar(vtype="B", name=f"q_{i}_{t}")

        z = {}
        for t in time_set:
            z[t] = m.addVar(vtype="B", name=f"z_{t}")

        F = {}
        for t in time_set:
            F[t] = m.addVar(vtype="C", lb=0.0, name=f"F_{t}")

        Cmax = m.addVar(vtype="C", lb=current_time, ub=T, name="Cmax")

        # ==============================================================
        # Basic assignment constraints
        # ==============================================================

        for i in installation_decision_modules:
            m.addCons(
                quicksum(x[i, t] for t in time_set) == 1,
                name=f"install_start_once_{i}",
            )

        for i in production_decision_modules:
            m.addCons(
                quicksum(q[i, t] for t in time_set) == 1,
                name=f"prod_start_once_{i}",
            )

        for i in arrival_decision_modules:
            m.addCons(
                quicksum(p[i, t] for t in time_set) == 1,
                name=f"arrival_once_{i}",
            )

        # ==============================================================
        # Installation precedence constraints
        # ==============================================================

        for i, j in self.E:

            # Already validated:
            # if j is completed or ongoing, then predecessor i must be completed.
            if j in completed_installations:
                continue

            if i in completed_installations:
                continue

            # Predecessor is ongoing installation.
            if i in ongoing_install_finish and j in installation_decision_modules:
                start_j = self._selected_time_expr(x, j, time_set)

                m.addCons(
                    ongoing_install_finish[i] <= start_j,
                    name=f"prec_ongoing_{i}_{j}",
                )
                continue

            # Both predecessor and successor need new installation decisions.
            if i in installation_decision_modules and j in installation_decision_modules:
                start_i = self._selected_time_expr(x, i, time_set)
                start_j = self._selected_time_expr(x, j, time_set)

                m.addCons(
                    start_i + d[i] <= start_j,
                    name=f"prec_{i}_{j}",
                )
                continue

            # If successor is ongoing, validation should already catch inconsistency.
            if j in ongoing_installation_modules:
                continue

        # ==============================================================
        # Installation state y[i,t]
        # ==============================================================

        for i in installation_decision_modules:
            for t in time_set:
                tau_min = max(current_time, t - d[i] + 1)

                if tau_min <= t:
                    m.addCons(
                        y[i, t] == quicksum(x[i, tau] for tau in range(tau_min, t + 1)),
                        name=f"in_install_{i}_{t}",
                    )
                else:
                    m.addCons(
                        y[i, t] == 0,
                        name=f"in_install_zero_{i}_{t}",
                    )

        # ==============================================================
        # Installation crew capacity
        # ==============================================================

        for t in time_set:
            occupied_by_ongoing = sum(
                1
                for _, finish_i in ongoing_install_finish.items()
                if current_time <= t < finish_i
            )

            m.addCons(
                quicksum(y[i, t] for i in installation_decision_modules)
                + occupied_by_ongoing
                <= self.C_install,
                name=f"crew_cap_{t}",
            )

        # ==============================================================
        # Arrival before installation
        # ==============================================================

        for i in installation_decision_modules:

            # Modules already at site do not need an arrival decision.
            if i in site_inventory_modules:
                continue

            install_start = self._selected_time_expr(x, i, time_set)
            arrival_time = self._selected_time_expr(p, i, time_set)

            m.addCons(
                arrival_time <= install_start,
                name=f"arrival_before_install_{i}",
            )

        # ==============================================================
        # Site inventory balance
        # I[i,t]: site inventory at the end of period t
        # ==============================================================

        for i in installation_decision_modules:

            initial_site_inventory = 1 if i in site_inventory_modules else 0

            arrival_now = p[i, current_time] if i in arrival_decision_modules else 0

            m.addCons(
                I[i, current_time]
                == initial_site_inventory + arrival_now - x[i, current_time],
                name=f"site_inv_init_{i}",
            )

            for t in range(current_time + 1, T + 1):
                arrival_t = p[i, t] if i in arrival_decision_modules else 0

                m.addCons(
                    I[i, t] == I[i, t - 1] + arrival_t - x[i, t],
                    name=f"site_inv_bal_{i}_{t}",
                )

        for t in time_set:
            m.addCons(
                quicksum(I[i, t] for i in installation_decision_modules)
                <= self.S_site,
                name=f"site_cap_{t}",
            )

        # ==============================================================
        # Production-to-arrival timing
        # ==============================================================

        for i in arrival_decision_modules:

            # Case 1:
            # Module still needs a new production start decision.
            if i in production_decision_modules:
                for t in time_set:
                    latest_prod_start = t - D[i] - L[i]

                    if latest_prod_start >= current_time:
                        m.addCons(
                            p[i, t]
                            <= quicksum(
                                q[i, tau]
                                for tau in range(current_time, latest_prod_start + 1)
                            ),
                            name=f"prod_to_arrive_{i}_{t}",
                        )
                    else:
                        m.addCons(
                            p[i, t] == 0,
                            name=f"too_early_arrive_{i}_{t}",
                        )

            # Case 2:
            # Module already produced and stored in factory.
            elif i in factory_inventory_modules:
                earliest_arrival = current_time + L[i]

                for t in time_set:
                    if t < earliest_arrival:
                        m.addCons(
                            p[i, t] == 0,
                            name=f"factory_inv_too_early_arrive_{i}_{t}",
                        )

            # Case 3:
            # Module is currently being produced.
            elif i in ongoing_production_modules:
                finish_i = ongoing_prod_finish[i]
                earliest_arrival = finish_i + L[i]

                for t in time_set:
                    if t < earliest_arrival:
                        m.addCons(
                            p[i, t] == 0,
                            name=f"ongoing_prod_too_early_arrive_{i}_{t}",
                        )

            else:
                raise ValueError(
                    f"Module {i} needs arrival but is neither production-decision, "
                    f"factory-inventory, nor ongoing-production."
                )

        # ==============================================================
        # Factory machine capacity
        # ==============================================================

        for t in time_set:
            occupied_by_ongoing_prod = sum(
                1
                for _, finish_i in ongoing_prod_finish.items()
                if current_time <= t < finish_i
            )

            m.addCons(
                quicksum(
                    q[i, tau]
                    for i in production_decision_modules
                    for tau in range(max(current_time, t - D[i] + 1), t + 1)
                )
                + occupied_by_ongoing_prod
                <= self.M_machine,
                name=f"machine_cap_{t}",
            )

        # ==============================================================
        # Arrival batch indicator
        # z[t] = 1 if any module arrives at site at t
        # ==============================================================

        for t in time_set:
            for i in arrival_decision_modules:
                m.addCons(
                    p[i, t] <= z[t],
                    name=f"link_arrival_batch_{i}_{t}",
                )

        # ==============================================================
        # Factory inventory balance
        # F[t]: aggregate factory inventory at the end of period t
        # ==============================================================

        initial_factory_inventory = len(factory_inventory_modules)

        for s in time_set:

            finished_from_new_prod = quicksum(
                q[i, s - D[i]]
                for i in production_decision_modules
                if s - D[i] in time_set
            )

            finished_from_ongoing = sum(
                1
                for _, finish_i in ongoing_prod_finish.items()
                if finish_i == s
            )

            # If module i arrives at site at s + L[i],
            # then it leaves the factory at time s.
            shipped_from_factory = quicksum(
                p[i, s + L[i]]
                for i in arrival_decision_modules
                if s + L[i] <= T
            )

            if s == current_time:
                m.addCons(
                    F[s]
                    == initial_factory_inventory
                    + finished_from_new_prod
                    + finished_from_ongoing
                    - shipped_from_factory,
                    name="factory_inv_init",
                )
            else:
                m.addCons(
                    F[s]
                    == F[s - 1]
                    + finished_from_new_prod
                    + finished_from_ongoing
                    - shipped_from_factory,
                    name=f"factory_inv_bal_{s}",
                )

            m.addCons(
                F[s] <= self.S_fac,
                name=f"factory_cap_{s}",
            )

        # ==============================================================
        # Project finish time
        # ==============================================================

        for i in installation_decision_modules:
            start_i = self._selected_time_expr(x, i, time_set)

            m.addCons(
                start_i + d[i] <= Cmax,
                name=f"finish_after_install_{i}",
            )

        for i, finish_i in ongoing_install_finish.items():
            m.addCons(
                finish_i <= Cmax,
                name=f"finish_after_ongoing_install_{i}",
            )

        # ==============================================================
        # Objective
        # ==============================================================

        order_cost = quicksum(
            self.OC * z[t]
            for t in time_set
        )

        factory_inventory_cost = quicksum(
            self.C_F * F[t]
            for t in time_set
        )

        onsite_inventory_cost = quicksum(
            self.C_O * I[i, t]
            for i in installation_decision_modules
            for t in time_set
        )

        indirect_cost = self.C_I * Cmax

        total_cost = (
            order_cost
            + factory_inventory_cost
            + onsite_inventory_cost
            + indirect_cost
        )

        m.setObjective(total_cost, "minimize")

        # Save model and variables
        self.m = m
        self.x = x
        self.y = y
        self.p = p
        self.I = I
        self.q = q
        self.z = z
        self.F = F
        self.Cmax = Cmax

        return m

    # ------------------------------------------------------------------
    # Solve and extract solution
    # ------------------------------------------------------------------

    def solve(self, time_limit=None, mip_gap=None):
        """
        Solve the re-optimization model.

        If the model has not been built, it will be built automatically.
        """

        if self.reopt_state is None:
            self.set_reoptimization_state({"current_time": 1})

        if self.m is None:
            self.build_reoptimization_model()

        if time_limit is not None:
            self._safe_set_param(self.m, "limits/time", float(time_limit))

        if mip_gap is not None:
            self._safe_set_param(self.m, "limits/gap", float(mip_gap))

        self.m.optimize()

        return self.m.getStatus()

    def get_objective_value(self):
        if self.m is None:
            return None

        if self.m.getNSols() == 0:
            return None

        return self.m.getObjVal()

    def _get_selected_time(self, var_dict, i, time_set):
        """
        Extract selected time from binary variables var[i,t].
        """
        for t in time_set:
            if self.m.getVal(var_dict[i, t]) > 0.5:
                return t
        return None

    def get_solution(self):
        """
        Extract solution from the solved re-optimization model.
        """

        if self.m is None:
            return None

        if self.m.getNSols() == 0:
            return None

        state = self.reopt_state
        current_time = state.current_time
        time_set = range(current_time, self.T + 1)

        solution = {
            "status": self.m.getStatus(),
            "objective": self.get_objective_value(),
            "finish_time": self.m.getVal(self.Cmax) if self.Cmax is not None else None,
            "installation_start": {},
            "production_start": {},
            "arrival_time": {},
            "arrival_batch_time": [],
            "factory_inventory": {},
            "site_inventory": {},
        }

        for i in self.installation_decision_modules:
            selected_t = self._get_selected_time(self.x, i, time_set)
            if selected_t is not None:
                solution["installation_start"][i] = selected_t

        for i in self.production_decision_modules:
            selected_t = self._get_selected_time(self.q, i, time_set)
            if selected_t is not None:
                solution["production_start"][i] = selected_t

        for i in self.arrival_decision_modules:
            selected_t = self._get_selected_time(self.p, i, time_set)
            if selected_t is not None:
                solution["arrival_time"][i] = selected_t

        for t in time_set:
            if self.m.getVal(self.z[t]) > 0.5:
                solution["arrival_batch_time"].append(t)

        for t in time_set:
            solution["factory_inventory"][t] = self.m.getVal(self.F[t])

        for i in self.installation_decision_modules:
            solution["site_inventory"][i] = {}
            for t in time_set:
                solution["site_inventory"][i][t] = self.m.getVal(self.I[i, t])

        return solution

    def get_solution_dict(self) -> Optional[Dict[str, Any]]:
        """
        Compatibility shape for previous callers.
        """
        solution = self.get_solution()
        if solution is None:
            return None

        site_inventory_flat = {}
        for i, t_vals in solution["site_inventory"].items():
            for t, inv in t_vals.items():
                if abs(inv) > 1e-6:
                    site_inventory_flat[(i, t)] = inv

        return {
            "objective": solution["objective"],
            "status": solution["status"],
            "installation_start": solution["installation_start"],
            "arrival_time": solution["arrival_time"],
            "production_start": solution["production_start"],
            "order_times": solution["arrival_batch_time"],
            "factory_inventory": {
                t: inv
                for t, inv in solution["factory_inventory"].items()
                if abs(inv) > 1e-6
            },
            "site_inventory": site_inventory_flat,
            "project_finish_time": solution["finish_time"],
        }

    def save_results_to_db(
        self,
        engine: Any,
        project_id: int,
        module_id_mapping: Optional[Dict[int, str]] = None,
        version_id: Optional[int] = None,
    ) -> bool:
        import pandas as pd
        from sqlalchemy import text

        solution = self.get_solution_dict()
        if solution is None:
            return False

        try:
            try:
                from .datamanager import ScheduleDataManager

                solution_table = ScheduleDataManager.solution_table_name(project_id)
                summary_table = ScheduleDataManager.summary_table_name(project_id)
                factory_inv_table = ScheduleDataManager.factory_inventory_table_name(project_id)
                site_inv_table = ScheduleDataManager.site_inventory_table_name(project_id)
            except ImportError:
                solution_table = f"solution_schedule_{project_id}"
                summary_table = f"optimization_summary_{project_id}"
                factory_inv_table = f"factory_inventory_{project_id}"
                site_inv_table = f"site_inventory_{project_id}"

            results_data = []
            for i in range(1, self.N + 1):
                module_id = module_id_mapping[i] if module_id_mapping else f"Module_{i}"
                install_start = solution["installation_start"].get(i)
                arrival_time = solution["arrival_time"].get(i)
                prod_start = solution["production_start"].get(i)
                prod_duration = self.D.get(i, 0)
                prod_finish = prod_start + prod_duration - 1 if prod_start else None
                factory_wait_start = prod_finish + 1 if prod_finish is not None else None
                onsite_wait_start = arrival_time
                onsite_wait_duration = (
                    install_start - onsite_wait_start
                    if install_start is not None and onsite_wait_start is not None
                    else None
                )
                transport_duration = self.L.get(i, 0)
                transport_start = (
                    arrival_time - transport_duration if arrival_time is not None else None
                )
                factory_wait_duration = (
                    transport_start - factory_wait_start
                    if transport_start is not None and factory_wait_start is not None
                    else None
                )
                install_duration = self.d.get(i, 0)
                install_finish = install_start + install_duration - 1 if install_start else None

                results_data.append(
                    {
                        "Module_ID": module_id,
                        "Module_Index": i,
                        "Installation_Start": install_start,
                        "Installation_Finish": install_finish,
                        "Installation_Duration": install_duration,
                        "Arrival_Time": arrival_time,
                        "Production_Start": prod_start,
                        "Production_Duration": self.D.get(i, 0),
                        "Factory_Wait_Start": factory_wait_start,
                        "Factory_Wait_Duration": factory_wait_duration,
                        "Onsite_Wait_Start": onsite_wait_start,
                        "Onsite_Wait_Duration": onsite_wait_duration,
                        "Transport_Start": transport_start,
                        "Transport_Duration": transport_duration,
                        "version_id": version_id,
                    }
                )

            results_df = pd.DataFrame(results_data)

            with engine.begin() as conn:
                from sqlalchemy import inspect

                inspector = inspect(engine)
                if solution_table in inspector.get_table_names():
                    columns = [col["name"] for col in inspector.get_columns(solution_table)]
                    if "version_id" not in columns:
                        conn.exec_driver_sql(
                            f'ALTER TABLE "{solution_table}" ADD COLUMN version_id INTEGER'
                        )
                    if version_id is not None:
                        delete_query = text(
                            f'DELETE FROM "{solution_table}" WHERE version_id = :version_id'
                        )
                        conn.execute(delete_query, {"version_id": version_id})
                    else:
                        delete_query = text(
                            f'DELETE FROM "{solution_table}" WHERE version_id IS NULL'
                        )
                        conn.execute(delete_query)

            results_df.to_sql(
                solution_table,
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1000,
            )

            summary_data = [
                {
                    "project_id": project_id,
                    "version_id": version_id,
                    "objective_value": solution["objective"],
                    "status": str(solution["status"]),
                    "project_finish_time": solution["project_finish_time"],
                    "num_orders": len(solution["order_times"]),
                    "order_times": ",".join(map(str, sorted(solution["order_times"]))),
                }
            ]
            summary_df = pd.DataFrame(summary_data)

            with engine.begin() as conn:
                from sqlalchemy import inspect as _inspect_summary

                inspector_summary = _inspect_summary(engine)
                if summary_table in inspector_summary.get_table_names():
                    summary_columns = [
                        col["name"] for col in inspector_summary.get_columns(summary_table)
                    ]
                    if "version_id" not in summary_columns:
                        conn.exec_driver_sql(
                            f'ALTER TABLE "{summary_table}" ADD COLUMN version_id INTEGER'
                        )
                    if version_id is not None:
                        delete_summary = text(
                            f'DELETE FROM "{summary_table}" WHERE version_id = :version_id'
                        )
                        conn.execute(delete_summary, {"version_id": version_id})
                    else:
                        delete_summary = text(
                            f'DELETE FROM "{summary_table}" WHERE version_id IS NULL'
                        )
                        conn.execute(delete_summary)

            summary_df.to_sql(summary_table, engine, if_exists="append", index=False)

            if solution["factory_inventory"]:
                factory_inv_data = [
                    {"time": t, "inventory_level": inv}
                    for t, inv in sorted(solution["factory_inventory"].items())
                ]
                pd.DataFrame(factory_inv_data).to_sql(
                    factory_inv_table, engine, if_exists="replace", index=False
                )

            if solution["site_inventory"]:
                site_inv_data = [
                    {"module_index": i, "time": t, "inventory_level": inv}
                    for (i, t), inv in sorted(solution["site_inventory"].items())
                ]
                pd.DataFrame(site_inv_data).to_sql(
                    site_inv_table, engine, if_exists="replace", index=False
                )
            return True
        except Exception:
            return False