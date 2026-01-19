from gurobipy import Model, GRB, quicksum
import pandas as pd
from sqlalchemy import Engine, text
from typing import Optional, Dict, Any
from datetime import date


def estimate_time_horizon(start_date: date, end_date: date, 
                         hours_per_day: float = 8.0,
                         safety_factor: float = 1.0) -> int:
    """
    Estimate time horizon T in working hours from project start/end dates.
    
    Simple approach: T = (end_date - start_date).days * hours_per_day * safety_factor
    
    Args:
        start_date: Project start date
        end_date: Project target end date
        hours_per_day: Average working hours per day (default 8.0)
        safety_factor: Safety factor to add buffer (default 1.2)
    
    Returns:
        T: Total number of working hours (time slots)
    """
    if end_date <= start_date:
        total_days = 1
    else:
        total_days = (end_date - start_date).days + 1
    
    # Simple calculation: days * hours_per_day * safety_factor
    raw_T = total_days * hours_per_day * safety_factor
    T = int(raw_T)
    if raw_T > T:  # ceil for non-integers
        T += 1
    
    return max(1, T)


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
                 OC,
                 C_I,
                 C_F, 
                 C_O):
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
        OC: cost per order batch
        C_I: penalty cost per unit time
        C_F: factory inventory cost per unit time per unit
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
        self.OC = OC
        self.C_I = C_I
        self.C_F = C_F
        self.C_O = C_O
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
        
        # Fixed constraints for re-optimization
        self.fixed_installation_starts = {}
        self.fixed_production_starts = {}
        self.fixed_arrival_times = {} 
        self.fixed_durations = {} # any problem here?
        self.reoptimize_from_time = None  # Current time (time index) from which to re-optimize
        # Lower bounds from START_POSTPONEMENT delays (for NOT_STARTED tasks)
        self.earliest_production_starts = {}
        self.earliest_transport_starts = {}
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
                             earliest_transport_starts: Optional[Dict[int, int]] = None,
                             earliest_installation_starts: Optional[Dict[int, int]] = None):
        """
        Set fixed constraints for re-optimization.
        
        Args:
            fixed_installation_starts: {module_index: start_time} (exact values for COMPLETED/IN_PROGRESS)
            fixed_production_starts: {module_index: start_time} (exact values for COMPLETED/IN_PROGRESS)
            fixed_arrival_times: {module_index: arrival_time} (exact values for COMPLETED/IN_PROGRESS)
            fixed_durations: {module_index: {phase: duration}}
            reoptimize_from_time: Current time (time index) from which to re-optimize
            earliest_production_starts: {module_index: earliest_start_time} (lower bounds for NOT_STARTED)
            earliest_transport_starts: {module_index: earliest_start_time} (lower bounds for NOT_STARTED)
            earliest_installation_starts: {module_index: earliest_start_time} (lower bounds for NOT_STARTED)
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
        if earliest_transport_starts:
            self.earliest_transport_starts = earliest_transport_starts.copy()
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

    def build_model(self):
        m = Model("prefab_with_factory_buffer")
        m.Params.TimeLimit = 120      # 最多算 120 秒
        m.Params.MIPGap    = 0.2     # 允许 20% 最优间隙
        m.Params.MIPFocus  = 1        # 更关注找可行解
        m.Params.Heuristics = 0.2     # 增强启发式（默认 0.05 左右）
        m.Params.Cuts = 0             # 如果节点过多，可以适当减弱 cuts
        # 利用多核
        m.Params.Threads = 0

        N, T = self.N, self.T
        d = self.d
        D = self.D
        L = self.L
        dummy_start = self.dummy_start
        dummy_end = self.dummy_end

        # ============ 3. variables ============
        # x[i,t] start installation (including dummy)
        x = {}
        for i in range(0, N + 2):
            for t in range(1, T + 1):
                x[i, t] = m.addVar(vtype=GRB.BINARY, name=f"x_{i}_{t}")

        # y[i,t] installing (only real activities)
        y = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                y[i, t] = m.addVar(vtype=GRB.BINARY, name=f"y_{i}_{t}")

        # p[i,t] arrival at site
        p = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                p[i, t] = m.addVar(vtype=GRB.BINARY, name=f"p_{i}_{t}")

        # site inventory
        I = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                I[i, t] = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, name=f"I_{i}_{t}")

        # factory production start
        q = {}
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                q[i, t] = m.addVar(vtype=GRB.BINARY, name=f"q_{i}_{t}")

        # order per time (batch)
        z = {}
        for t in range(1, T + 1):
            z[t] = m.addVar(vtype=GRB.BINARY, name=f"z_{t}")

        # factory inventory
        F = {}
        for s in range(1, T + 1):
            F[s] = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, name=f"F_{s}")

        m.update()

        # ============ 4. constraints ============

        # (1) dummy start fixed at time 1 (or reoptimize_from_time (current_time) if set)
        start_time = 1
        if self.reoptimize_from_time is not None:
            start_time = max(1, self.reoptimize_from_time)
        
        m.addConstr(x[dummy_start, start_time] == 1, "dummy_start_fix")
        for t in range(1, T + 1):
            if t != start_time:
                m.addConstr(x[dummy_start, t] == 0, f"dummy_start_zero_{t}")

        # (2) each real activity starts once
        for i in range(1, N + 1):
            m.addConstr(quicksum(x[i, t] for t in range(1, T + 1)) == 1,
                        f"start_once_{i}")
        
        # (2a) Fixed installation starts (for re-optimization)
        # Note: Since we have sum(x[i, t]) = 1, fixing x[i, fixed_start] = 1 
        # automatically forces all other x[i, t] = 0
        # IMPORTANT: Fixed starts for COMPLETED/IN_PROGRESS tasks must be <= current_time (reoptimize_from_time)
        # This ensures we don't schedule tasks in the past
        for i, fixed_start in self.fixed_installation_starts.items():
            if 1 <= i <= N and 1 <= fixed_start <= T:
                # Validate: fixed_start should be <= reoptimize_from_time (for COMPLETED/IN_PROGRESS tasks)
                if self.reoptimize_from_time is not None and fixed_start > self.reoptimize_from_time:
                    print(f"[WARNING] Fixed installation start {fixed_start} for module {i} is after current_time {self.reoptimize_from_time}. This may indicate an error in state identification.")
                    # Still add the constraint, but log a warning
                m.addConstr(x[i, fixed_start] == 1, f"fixed_install_start_{i}")
        
        # (2b) Fixed production starts
        # Note: Since we have sum(q[i, t]) = 1, fixing q[i, fixed_start] = 1
        # automatically forces all other q[i, t] = 0
        for i, fixed_start in self.fixed_production_starts.items():
            if 1 <= i <= N and 1 <= fixed_start <= T:
                # Validate: fixed_start should be <= reoptimize_from_time (for COMPLETED/IN_PROGRESS tasks)
                if self.reoptimize_from_time is not None and fixed_start > self.reoptimize_from_time:
                    print(f"[WARNING] Fixed production start {fixed_start} for module {i} is after current_time {self.reoptimize_from_time}. This may indicate an error in state identification.")
                m.addConstr(q[i, fixed_start] == 1, f"fixed_prod_start_{i}")
        
        # (2c) Fixed arrival times
        # Note: Since we have sum(p[i, t]) = 1, fixing p[i, fixed_arrival] = 1
        # automatically forces all other p[i, t] = 0
        for i, fixed_arrival in self.fixed_arrival_times.items():
            if 1 <= i <= N and 1 <= fixed_arrival <= T:
                # Validate: fixed_arrival should be <= reoptimize_from_time (for COMPLETED/IN_PROGRESS tasks)
                if self.reoptimize_from_time is not None and fixed_arrival > self.reoptimize_from_time:
                    print(f"[WARNING] Fixed arrival time {fixed_arrival} for module {i} is after current_time {self.reoptimize_from_time}. This may indicate an error in state identification.")
                m.addConstr(p[i, fixed_arrival] == 1, f"fixed_arrival_{i}")
        
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

        # (2e) Prevent NOT_STARTED tasks from starting before reoptimize_from_time
        # For re-optimization: all unfixed tasks (NOT_STARTED) cannot start before current_time
        # This ensures that we cannot schedule tasks in the past
        if self.reoptimize_from_time is not None and self.reoptimize_from_time > 1:
            min_time = self.reoptimize_from_time
            # Prevent installation starts before current_time for unfixed tasks
            for i in range(1, N + 1):
                if i not in self.fixed_installation_starts:
                    for t in range(1, min_time):
                        m.addConstr(x[i, t] == 0, f"no_install_before_current_{i}_{t}")
            
            # Prevent production starts before current_time for unfixed tasks
            for i in range(1, N + 1):
                if i not in self.fixed_production_starts:
                    for t in range(1, min_time):
                        m.addConstr(q[i, t] == 0, f"no_prod_before_current_{i}_{t}")
            
            # Prevent arrival times before current_time for unfixed tasks
            for i in range(1, N + 1):
                if i not in self.fixed_arrival_times:
                    for t in range(1, min_time):
                        m.addConstr(p[i, t] == 0, f"no_arrival_before_current_{i}_{t}")

        # (2f) Lower bounds from START_POSTPONEMENT delays (for NOT_STARTED tasks)
        # These are lower bounds, not fixed values - tasks can start at or after these times
        # Production lower bounds
        for i, earliest_start in self.earliest_production_starts.items():
            if 1 <= i <= N and 1 <= earliest_start <= T:
                # Only apply if not already fixed (NOT_STARTED tasks)
                if i not in self.fixed_production_starts:
                    for t in range(1, earliest_start):
                        m.addConstr(q[i, t] == 0, f"earliest_prod_lb_{i}_{t}")
        
        # Transport lower bounds (constraint on arrival time, which implies transport start)
        # Note: Transport start = arrival_time - L[i], so we constrain arrival time
        for i, earliest_start in self.earliest_transport_starts.items():
            if 1 <= i <= N and 1 <= earliest_start <= T:
                # Only apply if not already fixed (NOT_STARTED tasks)
                if i not in self.fixed_arrival_times:
                    # Earliest_Transport_Start refers to when transport can start
                    # Arrival time = transport_start + L[i]
                    # So earliest arrival >= earliest_transport_start + L[i]
                    earliest_arrival = earliest_start + self.L.get(i, 0)
                    if 1 <= earliest_arrival <= T:
                        for t in range(1, earliest_arrival):
                            m.addConstr(p[i, t] == 0, f"earliest_arrival_lb_{i}_{t}")
        
        # Installation lower bounds
        for i, earliest_start in self.earliest_installation_starts.items():
            if 1 <= i <= N and 1 <= earliest_start <= T:
                # Only apply if not already fixed (NOT_STARTED tasks)
                if i not in self.fixed_installation_starts:
                    for t in range(1, earliest_start):
                        m.addConstr(x[i, t] == 0, f"earliest_install_lb_{i}_{t}")

        # (3) dummy end starts once
        m.addConstr(quicksum(x[dummy_end, t] for t in range(1, T + 1)) == 1,
                    "dummy_end_once")

        # (4) precedence between real activities
        for (i, j) in self.E:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            start_j = quicksum(t * x[j, t] for t in range(1, T + 1))
            m.addConstr(start_i + d[i] <= start_j, f"prec_{i}_{j}")

        # (5) roots after dummy start
        for i in self.roots:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            m.addConstr(1 <= start_i, f"root_after_dummy_{i}")

        # (6) leaves before dummy end
        for i in self.leaves:
            start_i = quicksum(t * x[i, t] for t in range(1, T + 1))
            end_d = quicksum(t * x[dummy_end, t] for t in range(1, T + 1))
            m.addConstr(start_i + d[i] <= end_d, f"leaf_before_dummy_end_{i}")

        # (7) installation state
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                tau_min = max(1, t - d[i] + 1)
                m.addConstr(
                    y[i, t] == quicksum(x[i, tau] for tau in range(tau_min, t + 1)),
                    f"in_install_{i}_{t}"
                )

        # (8) installation crew capacity
        for t in range(1, T + 1):
            m.addConstr(quicksum(y[i, t] for i in range(1, N + 1)) <= self.C_install,
                        f"crew_{t}")

        # (9) arrival once
        for i in range(1, N + 1):
            m.addConstr(quicksum(p[i, t] for t in range(1, T + 1)) == 1,
                        f"arrive_once_{i}")

        # (10) arrival no later than installation start
        for i in range(1, N + 1):
            arr = quicksum(t * p[i, t] for t in range(1, T + 1))
            sta = quicksum(t * x[i, t] for t in range(1, T + 1))
            m.addConstr(arr <= sta, f"arrive_before_install_{i}")

        # (11) site inventory balance
        for i in range(1, N + 1):
            # t = 1
            m.addConstr(I[i, 1] == p[i, 1] - x[i, 1], f"site_inv_init_{i}")
            # t >= 2
            for t in range(2, T + 1):
                m.addConstr(
                    I[i, t] == I[i, t - 1] + p[i, t] - x[i, t],
                    f"site_inv_bal_{i}_{t}"
                )

        # (12) site warehouse capacity
        for t in range(1, T + 1):
            m.addConstr(
                quicksum(I[i, t] for i in range(1, N + 1)) <= self.S_site,
                f"site_cap_{t}"
            )

        # (13) production -> arrival timing
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                latest_prod = t - D[i] - L[i]
                if latest_prod >= 1:
                    m.addConstr(
                        p[i, t] <= quicksum(q[i, tau] for tau in range(1, latest_prod + 1)),
                        f"prod_to_arrive_{i}_{t}"
                    )
                else:
                    # cannot arrive this early
                    m.addConstr(p[i, t] == 0, f"too_early_arrive_{i}_{t}")

        # (14) factory machine capacity
        for t in range(1, T + 1):
            m.addConstr(
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
                m.addConstr(p[i, t] <= z[t], f"link_order_{i}_{t}")

        # (16) factory inventory: F1 = 0
        m.addConstr(F[1] == 0, "factory_init")

        # (17) factory inventory recursion
        for s in range(2, T + 1):
            finished_here = quicksum(
                q[i, s - D[i]] for i in range(1, N + 1) if s - D[i] >= 1
            )
            shipped_here = quicksum(
                p[i, s + L[i]] for i in range(1, N + 1) if s + L[i] <= T
            )
            m.addConstr(
                F[s] == F[s - 1] + finished_here - shipped_here,
                f"factory_inv_bal_{s}"
            )

        # (18) factory buffer capacity
        for s in range(1, T + 1):
            m.addConstr(F[s] <= self.S_fac, f"factory_cap_{s}")

        # ============ 5. objective ============
        finish_time = quicksum(t * x[dummy_end, t] for t in range(1, T + 1))
        order_cost = quicksum(self.OC * z[t] for t in range(1, T + 1))
        factory_cost = quicksum(self.C_F * F[s] for s in range(1, T + 1))
        onsite_cost = quicksum(self.C_O * I[i, t] for i in range(1, N + 1) for t in range(1, T + 1))
        indirect_cost = self.C_I * finish_time

        m.setObjective(order_cost + factory_cost + onsite_cost + indirect_cost, GRB.MINIMIZE) # add onsite cost

        # 保存对象
        self.m = m
        self.x = x
        self.y = y
        self.p = p
        self.I = I
        self.q = q
        self.z = z
        self.F = F

        return m

    def solve(self, time_limit=None, mip_gap=None):
        if self.m is None:
            self.build_model()

        if time_limit is not None: # consider the possible change later
            self.m.Params.TimeLimit = time_limit
        if mip_gap is not None:
            self.m.Params.MIPGap = mip_gap

        self.m.optimize()
        return self.m.Status

    def get_solution_dict(self) -> Optional[Dict[str, Any]]:
        """
        Extract solution values from the solved model.
        Returns a dictionary with all solution data, or None if model not solved.
        """
        if self.m is None:
            return None
        
        if self.m.Status not in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
            return None
        
        solution = {
            'objective': self.m.ObjVal, # just the objective value, not the total cost
            'status': self.m.Status,
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
        
        # Extract installation start times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if x[i, t].X > 0.5:
                    solution['installation_start'][i] = t
                    break
        
        # Extract arrival times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if p[i, t].X > 0.5:
                    solution['arrival_time'][i] = t
                    break
        
        # Extract production start times
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if q[i, t].X > 0.5:
                    solution['production_start'][i] = t
                    break
        
        # Extract order times
        for t in range(1, T + 1):
            if z[t].X > 0.5:
                solution['order_times'].append(t)
        
        # Extract factory inventory
        for s in range(1, T + 1):
            if F[s].X > 1e-6:
                solution['factory_inventory'][s] = F[s].X
        
        # Extract site inventory
        for i in range(1, N + 1):
            for t in range(1, T + 1):
                if self.I[i, t].X > 1e-6:
                    solution['site_inventory'][(i, t)] = self.I[i, t].X
        
        # Extract project finish time
        for t in range(1, T + 1):
            if x[dummy_end, t].X > 0.5:
                solution['project_finish_time'] = t
                break
        
        return solution

    def save_results_to_db(self, 
                          engine: Engine, 
                          project_id: int,
                          module_id_mapping: Optional[Dict[int, str]] = None,
                          version_id: Optional[int] = None,
                          earliest_start_columns: Optional[pd.DataFrame] = None) -> bool:
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
            
            # Merge Earliest_* columns from earliest_start_columns if provided
            # These columns represent lower bounds from START_POSTPONEMENT delays
            if earliest_start_columns is not None and not earliest_start_columns.empty:
                # Extract only Earliest_* columns and Module_ID for merging
                earliest_cols_to_merge = []
                for col in ['Earliest_Production_Start', 'Earliest_Transport_Start', 'Earliest_Installation_Start']:
                    if col in earliest_start_columns.columns:
                        earliest_cols_to_merge.append(col)
                
                if earliest_cols_to_merge:
                    # Create a subset DataFrame with only Module_ID and Earliest_* columns
                    merge_cols = ['Module_ID'] + earliest_cols_to_merge
                    earliest_df = earliest_start_columns[merge_cols].copy()
                    # Merge on Module_ID (left join to keep all results_df rows)
                    results_df = results_df.merge(
                        earliest_df,
                        on='Module_ID',
                        how='left'
                    )
            
            # Ensure solution table has version_id column and Earliest_* columns, and delete old data for this version if table exists
            with engine.begin() as conn:
                from sqlalchemy import inspect
                inspector = inspect(engine)
                if solution_table in inspector.get_table_names():
                    # Table exists: check if required columns exist and add if needed
                    columns = [col['name'] for col in inspector.get_columns(solution_table)]
                    
                    # Add version_id column if missing
                    if 'version_id' not in columns:
                        conn.exec_driver_sql(f'ALTER TABLE "{solution_table}" ADD COLUMN version_id INTEGER')
                    
                    # Add Earliest_* columns if missing (for START_POSTPONEMENT delay lower bounds)
                    earliest_columns = {
                        'Earliest_Production_Start': 'INTEGER',
                        'Earliest_Transport_Start': 'INTEGER',
                        'Earliest_Installation_Start': 'INTEGER'
                    }
                    for col_name, col_type in earliest_columns.items():
                        if col_name not in columns:
                            conn.exec_driver_sql(f'ALTER TABLE "{solution_table}" ADD COLUMN "{col_name}" {col_type}')
                            print(f"[DEBUG] Added column {col_name} to {solution_table}")
                    
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
            import traceback
            print(f"[ERROR] Error saving results to database: {e}")
            print(f"[ERROR] Traceback:")
            traceback.print_exc()
            return False
