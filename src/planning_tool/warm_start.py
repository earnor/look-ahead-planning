"""
Constructive heuristic used to size the time horizon.

The three-stage scheduling logic
(just-in-time truck arrival, backward production, forward installation) is kept
as-is; the precedence source is the planning tool's own arc list.

The makespan of this schedule chooses a tight horizon T for the CP-SAT model.
The same schedule is also handed to CP-SAT as a solution hint, so search
starts from a feasible incumbent instead of from scratch.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

# A truck carries between MIN and MAX modules. The last load of the project may
# be smaller, because the module count rarely divides evenly.
MIN_BATCH_SIZE = 3
MAX_BATCH_SIZE = 5

# Extra room given to the solver on top of the heuristic makespan, so it can
# move work around instead of only reproducing the schedule it was handed.
HORIZON_SLACK_RATIO = 0.25


@dataclass
class Batch:
    batch_id: int
    modules: List[int]
    arrival_time: Optional[int] = None


@dataclass
class HeuristicSolution:
    install_start: Dict[int, int]
    install_finish: Dict[int, int]
    arrival_time: Dict[int, int]
    prod_start: Dict[int, int]
    batch_arrival: Dict[int, int]
    batch_of_module: Dict[int, int]
    batches: List[Batch] = field(default_factory=list)
    cmax: int = 0


# ============================================================
# Interval / storage bookkeeping
# ============================================================

def can_place_interval(usage: Dict[int, int], start: int, duration: int, capacity: int) -> bool:
    for t in range(start, start + duration):
        if usage.get(t, 0) + 1 > capacity:
            return False
    return True


def place_interval(usage: Dict[int, int], start: int, duration: int) -> None:
    for t in range(start, start + duration):
        usage[t] = usage.get(t, 0) + 1


def can_add_storage(usage: Dict[int, int], start: int, end: int, capacity: int) -> bool:
    if start >= end:
        return True
    for t in range(start, end):
        if usage.get(t, 0) + 1 > capacity:
            return False
    return True


def add_storage(usage: Dict[int, int], start: int, end: int) -> None:
    if start >= end:
        return
    for t in range(start, end):
        usage[t] = usage.get(t, 0) + 1


def release_storage(usage: Dict[int, int], start: int, end: int) -> None:
    if start >= end:
        return
    for t in range(start, end):
        usage[t] = usage.get(t, 0) - 1


def storage_within_capacity(usage: Dict[int, int], start: int, end: int, capacity: int) -> bool:
    """Check occupancy that has already been reserved, without adding a new unit."""
    if start >= end:
        return True
    for t in range(start, end):
        if usage.get(t, 0) > capacity:
            return False
    return True


# ============================================================
# Batching
# ============================================================

def choose_batch_sizes(n: int, min_batch_size: int, max_batch_size: int) -> List[int]:
    """
    Split n modules into loads of [min_batch_size, max_batch_size].
    A final leftover smaller than min_batch_size is shipped as its own load.
    """
    if n <= 0:
        return []
    if n < min_batch_size:
        return [n]

    sizes: List[int] = []
    remaining = n
    while remaining > 0:
        if min_batch_size <= remaining <= max_batch_size:
            sizes.append(remaining)
            remaining = 0
        elif remaining > max_batch_size:
            # Prefer full loads, but avoid stranding fewer than min_batch_size.
            candidate = max_batch_size
            while candidate > min_batch_size:
                rest = remaining - candidate
                if rest == 0 or rest >= min_batch_size:
                    break
                candidate -= 1
            sizes.append(candidate)
            remaining -= candidate
        else:
            sizes.append(remaining)
            remaining = 0
    return sizes


# ============================================================
# Precedence order
# ============================================================

def topological_order(
    node_ids: Iterable[int],
    arcs: Iterable[Tuple[int, int]],
    d: Dict[int, int],
    D: Dict[int, int],
    L: Dict[int, int],
) -> List[int]:
    """
    Construction order. Modules are taken by precedence depth first (all
    currently independent roots before any of their successors), so several
    installation crews can start in parallel. Within a depth layer: longest
    downstream chain, then longest installation, then longest production +
    transport, then module index for stability.
    """
    node_ids = list(node_ids)
    succ: Dict[int, List[int]] = defaultdict(list)
    indeg: Dict[int, int] = {n: 0 for n in node_ids}
    for p, s in set(arcs):
        succ[p].append(s)
        indeg[s] = indeg.get(s, 0) + 1

    plain_indeg = dict(indeg)
    plain_q = deque([n for n in node_ids if plain_indeg[n] == 0])
    plain_order: List[int] = []
    while plain_q:
        u = plain_q.popleft()
        plain_order.append(u)
        for v in succ[u]:
            plain_indeg[v] -= 1
            if plain_indeg[v] == 0:
                plain_q.append(v)
    if len(plain_order) != len(node_ids):
        raise ValueError("Installation precedence contains a cycle.")

    downstream = {n: 0 for n in node_ids}
    for u in reversed(plain_order):
        downstream[u] = max((1 + downstream[v] for v in succ[u]), default=0)

    depth = {n: 0 for n in node_ids}
    for u in plain_order:
        for v in succ[u]:
            depth[v] = max(depth[v], depth[u] + 1)

    def key(n: int) -> Tuple:
        return (depth[n], -downstream[n], -d[n], -(D[n] + L[n]), n)

    heap: List[Tuple[Tuple, int]] = []
    work_indeg = dict(indeg)
    for n in node_ids:
        if work_indeg[n] == 0:
            heapq.heappush(heap, (key(n), n))

    order: List[int] = []
    while heap:
        _, u = heapq.heappop(heap)
        order.append(u)
        for v in succ[u]:
            work_indeg[v] -= 1
            if work_indeg[v] == 0:
                heapq.heappush(heap, (key(v), v))
    return order


# ============================================================
# Three-stage scheduling
# ============================================================

def try_schedule_batch_production(
    batch: Batch,
    arrival_time: int,
    D: Dict[int, int],
    L: Dict[int, int],
    current_time: int,
    T: int,
    M_machine: int,
    S_fac: int,
    machine_usage: Dict[int, int],
    factory_storage_usage: Dict[int, int],
) -> Optional[Dict[int, int]]:
    """
    Schedule production for a whole truck load, backwards from its arrival.
    Nothing is committed unless every module in the load fits.
    """
    temp_machine_usage = dict(machine_usage)
    temp_factory_storage_usage = dict(factory_storage_usage)
    temp_prod_start: Dict[int, int] = {}

    modules_sorted = sorted(batch.modules, key=lambda i: arrival_time - L[i] - D[i])

    for i in modules_sorted:
        latest_q = arrival_time - L[i] - D[i]
        if latest_q < current_time:
            return None

        placed = False
        for q in range(latest_q, current_time - 1, -1):
            finish = q + D[i]
            depart = arrival_time - L[i]
            if finish > depart or finish > T or arrival_time > T:
                continue
            if not can_place_interval(temp_machine_usage, q, D[i], M_machine):
                continue
            if not can_add_storage(temp_factory_storage_usage, finish, depart, S_fac):
                continue

            temp_prod_start[i] = q
            place_interval(temp_machine_usage, q, D[i])
            add_storage(temp_factory_storage_usage, finish, depart)
            placed = True
            break

        if not placed:
            return None

    return temp_prod_start


def commit_batch_production(
    prod_start_for_batch: Dict[int, int],
    arrival_time: int,
    D: Dict[int, int],
    L: Dict[int, int],
    machine_usage: Dict[int, int],
    factory_storage_usage: Dict[int, int],
) -> None:
    for i, q in prod_start_for_batch.items():
        place_interval(machine_usage, q, D[i])
        add_storage(factory_storage_usage, q + D[i], arrival_time - L[i])


def schedule_batch_arrival_and_production(
    batch: Batch,
    target_arrival: int,
    D: Dict[int, int],
    L: Dict[int, int],
    current_time: int,
    T: int,
    M_machine: int,
    S_fac: int,
    S_site: int,
    machine_usage: Dict[int, int],
    factory_storage_usage: Dict[int, int],
    site_storage_usage: Dict[int, int],
    arrival_period_load: Dict[int, int],
    max_modules_per_arrival_period: int,
    max_delay_per_batch: int,
) -> Tuple[Optional[int], Optional[Dict[int, int]]]:
    """
    Earliest arrival at or after `target_arrival` at which the whole load fits
    on site and can be produced in time.
    """
    start = max(target_arrival, current_time)
    size = len(batch.modules)

    for delay in range(max_delay_per_batch + 1):
        A = start + delay
        if A > T:
            break
        # One truck per arrival period, mirroring the MIP's load constraint.
        if arrival_period_load.get(A, 0) + size > max_modules_per_arrival_period:
            continue
        # The truck unloads the whole batch into the yard at once. Occupancy only
        # falls between two deliveries, so checking the arrival period is enough
        # as long as deliveries are scheduled in order.
        if site_storage_usage.get(A, 0) + size > S_site:
            continue

        prod_plan = try_schedule_batch_production(
            batch=batch, arrival_time=A, D=D, L=L, current_time=current_time, T=T,
            M_machine=M_machine, S_fac=S_fac,
            machine_usage=machine_usage, factory_storage_usage=factory_storage_usage,
        )
        if prod_plan is not None:
            return A, prod_plan

    return None, None


def find_module_install_time(
    i: int,
    earliest: int,
    arrival: int,
    d: Dict[int, int],
    T: int,
    C_install: int,
    S_site: int,
    crew_usage: Dict[int, int],
    site_storage_usage: Dict[int, int],
) -> Optional[int]:
    """Earliest installation start >= `earliest` that respects crew and yard capacity."""
    t = earliest
    while t + d[i] <= T:
        if (can_place_interval(crew_usage, t, d[i], C_install)
                and storage_within_capacity(site_storage_usage, arrival, t, S_site)):
            return t
        t += 1
    return None


def trivial_horizon_bound(N: int, d: Dict[int, int], D: Dict[int, int], L: Dict[int, int]) -> int:
    """
    A horizon that always admits a schedule: produce, ship and install every
    module one after another. Only used to give the heuristic room to work.
    """
    total = sum(D[i] + L[i] + d[i] for i in range(1, N + 1))
    return max(1, total + N + 1)


def horizon_from_makespan(cmax: int, slack_ratio: float = HORIZON_SLACK_RATIO) -> int:
    """Time horizon handed to the solver, derived from the heuristic makespan."""
    return max(1, int(math.ceil(cmax * (1.0 + slack_ratio))) + 1)


def horizon_from_remaining(
    tau: int,
    remaining: int,
    slack_ratio: float = HORIZON_SLACK_RATIO,
) -> int:
    """
    Absolute horizon for re-optimization: tau plus 25% slack on
    (remaining work after tau + the new delay hours).
    The extra +1 is the dummy end period, matching horizon_from_makespan.
    """
    extra = int(math.ceil(max(0, remaining) * (1.0 + slack_ratio))) + 1
    return max(1, int(tau) + extra)


def reference_values(
    solution: HeuristicSolution,
    D: Dict[int, int],
    L: Dict[int, int],
) -> Dict[str, float]:
    """
    Size of each objective term in the heuristic schedule.

    Duration and transport keep their own scale in the model. The two storage
    numbers are stored separately for diagnostics; the model divides both
    inventory terms by their sum, so a JIT heuristic (factory wait = 0)
    cannot make factory storage look more expensive than site storage.
    """
    modules = list(solution.install_start.keys())
    site = sum(solution.install_start[i] - solution.arrival_time[i] for i in modules)
    factory = sum(
        (solution.arrival_time[i] - L[i]) - (solution.prod_start[i] + D[i])
        for i in modules
    )
    return {
        "ref_duration": float(solution.cmax),
        "ref_transport": float(len(set(solution.arrival_time.values()))),
        "ref_site_storage": float(site),
        "ref_factory_storage": float(factory),
    }


def construct_solution(
    N: int,
    E: List[Tuple[int, int]],
    d: Dict[int, int],
    D: Dict[int, int],
    L: Dict[int, int],
    C_install: int,
    M_machine: int,
    S_site: int,
    S_fac: int,
    current_time: int = 1,
    T: Optional[int] = None,
    min_batch_size: int = MIN_BATCH_SIZE,
    max_batch_size: int = MAX_BATCH_SIZE,
    max_delay_per_batch: int = 500,
) -> HeuristicSolution:
    """
    Build a feasible schedule: modules are grouped into truck loads along the
    construction order, each load arrives just in time for its first module and
    is produced backwards from that arrival, then modules are installed forwards.

    Time indices follow the planner: the first period is 1 and everything must
    finish by T.
    """
    modules = list(range(1, N + 1))
    if not modules:
        return HeuristicSolution({}, {}, {}, {}, {}, {}, [], current_time)

    if T is None:
        T = trivial_horizon_bound(N, d, D, L)

    # Batch sizes stay exactly as in the MIP; a schedule built with different
    # loads could not be used as a starting solution.
    order = topological_order(modules, E, d, D, L)

    predecessors: Dict[int, set] = defaultdict(set)
    for p, s in E:
        predecessors[s].add(p)

    # Ship every independent root on the first truck so parallel crews can
    # start together. Remaining modules keep the usual 3-5 packing and a
    # single leftover load.
    n_roots = sum(1 for i in order if not predecessors[i])
    rest_n = len(order) - n_roots
    if min_batch_size <= n_roots <= max_batch_size and rest_n > 0:
        sizes = [n_roots] + choose_batch_sizes(rest_n, min_batch_size, max_batch_size)
    else:
        sizes = choose_batch_sizes(len(order), min_batch_size, max_batch_size)
    batches: List[Batch] = []
    batch_of_module: Dict[int, int] = {}
    cursor = 0
    for bid, size in enumerate(sizes):
        mods = order[cursor:cursor + size]
        cursor += size
        batches.append(Batch(batch_id=bid, modules=mods))
        for i in mods:
            batch_of_module[i] = bid
    batch_by_id = {b.batch_id: b for b in batches}

    install_start: Dict[int, int] = {}
    install_finish: Dict[int, int] = {}
    arrival_time: Dict[int, int] = {}
    prod_start: Dict[int, int] = {}
    batch_arrival: Dict[int, int] = {}

    crew_usage: Dict[int, int] = {}
    machine_usage: Dict[int, int] = {}
    factory_storage_usage: Dict[int, int] = {}
    site_storage_usage: Dict[int, int] = {}
    arrival_period_load: Dict[int, int] = {}

    # A module occupies the yard from arrival until it is installed. Occupancy is
    # reserved to the horizon and released again at the installation start.
    yard_horizon = T + 1
    last_arrival = current_time

    for i in order:
        pred_lb = current_time
        for p in predecessors[i]:
            pred_lb = max(pred_lb, install_finish[p])

        b_id = batch_of_module[i]
        if b_id not in batch_arrival:
            batch = batch_by_id[b_id]
            # Loads are built along the installation order, so they are delivered
            # in that order too.
            A, prod_plan = schedule_batch_arrival_and_production(
                batch=batch, target_arrival=max(pred_lb, last_arrival), D=D, L=L,
                current_time=current_time, T=T, M_machine=M_machine,
                S_fac=S_fac, S_site=S_site,
                machine_usage=machine_usage,
                factory_storage_usage=factory_storage_usage,
                site_storage_usage=site_storage_usage,
                arrival_period_load=arrival_period_load,
                max_modules_per_arrival_period=max_batch_size,
                max_delay_per_batch=max_delay_per_batch,
            )
            if A is None:
                raise RuntimeError(
                    f"Could not schedule delivery for truck load {b_id} "
                    f"(modules {batch.modules}). Check machine, factory and site capacity."
                )

            commit_batch_production(prod_plan, A, D, L, machine_usage, factory_storage_usage)
            prod_start.update(prod_plan)
            batch_arrival[b_id] = A
            batch.arrival_time = A
            last_arrival = A
            arrival_period_load[A] = arrival_period_load.get(A, 0) + len(batch.modules)
            for j in batch.modules:
                arrival_time[j] = A
                add_storage(site_storage_usage, A, yard_horizon)

        A_i = arrival_time[i]
        t = find_module_install_time(
            i=i, earliest=max(pred_lb, A_i), arrival=A_i, d=d, T=T,
            C_install=C_install, S_site=S_site,
            crew_usage=crew_usage, site_storage_usage=site_storage_usage,
        )
        if t is None:
            raise RuntimeError(
                f"Could not place installation for module {i}. "
                f"Check crew count and site storage capacity."
            )

        place_interval(crew_usage, t, d[i])
        release_storage(site_storage_usage, t, yard_horizon)
        install_start[i] = t
        install_finish[i] = t + d[i]

    cmax = max(install_finish.values()) if install_finish else current_time

    return HeuristicSolution(
        install_start=install_start,
        install_finish=install_finish,
        arrival_time=arrival_time,
        prod_start=prod_start,
        batch_arrival=batch_arrival,
        batch_of_module=batch_of_module,
        batches=batches,
        cmax=cmax,
    )
