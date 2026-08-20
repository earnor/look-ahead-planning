"""Remaining-horizon formula for re-optimization, plus a small MIP check."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planning_tool.rescheduler import TaskState, remaining_periods_after_tau  # noqa: E402
from planning_tool.warm_start import horizon_from_remaining  # noqa: E402


def st(module_id, index, phase, status, start=None, finish=None, actual=None):
    return TaskState(
        module_id=module_id,
        module_index=index,
        phase=phase,
        status=status,
        start_time=start,
        finish_time=finish,
        progress=1.0 if status == "COMPLETED" else (0.5 if status == "IN_PROGRESS" else 0.0),
        actual_start_time=actual if actual is not None else start,
    )


def phases(module_id, index, fab, trans, inst):
    return [
        st(module_id, index, "FABRICATION", *fab),
        st(module_id, index, "TRANSPORT", *trans),
        st(module_id, index, "INSTALLATION", *inst),
    ]


# --- 1. leftover install queue from tau, plus a not-started successor ---
tau = 10
states = {
    "A": phases("A", 1,
                ("COMPLETED", 1, 2),
                ("COMPLETED", 3, 3),
                ("COMPLETED", 4, 7)),
    "B": phases("B", 2,
                ("COMPLETED", 2, 3),
                ("COMPLETED", 4, 4),
                ("IN_PROGRESS", 8, 12, 8)),
    "C": phases("C", 3,
                ("NOT_STARTED",),
                ("NOT_STARTED",),
                ("NOT_STARTED",)),
}
D = {1: 2, 2: 2, 3: 2}
L = {1: 1, 2: 1, 3: 1}
d = {1: 4, 2: 5, 3: 4}  # B elapsed 2, remaining install 3
E = [(1, 2), (2, 3)]
remaining = remaining_periods_after_tau(states, tau, D, L, d, E, C_install=1, M_machine=2)
# chain: B rem 3, then C needs D+L+d = 7, but C waits for B -> 3 + 4 = 7
# crew: 0+3+4 = 7
assert remaining == 7, remaining
T = horizon_from_remaining(tau, remaining)
assert T == 10 + 9 + 1, T  # ceil(7*1.25)=9, +1 dummy
print(f"case leftover queue: remaining={remaining}, T={T}")

# --- 2. remaining uses undelayed durations; delay hours are added afterwards ---
T_with_delay = horizon_from_remaining(tau, remaining + 3)
assert T_with_delay == 10 + 13 + 1, T_with_delay  # ceil(10*1.25)=13
print(f"case remaining+delay: remaining={remaining}, delay=3, T={T_with_delay}")

# --- 3. start postponement of a not-started module ---
d[2] = 5
earliest = {3: {"FABRICATION": 16}}  # 6 periods after tau before C can start
remaining_post = remaining_periods_after_tau(
    states, tau, D, L, d, E, C_install=1, earliest_starts=earliest
)
# C: offset 6 + 2+1+4 = 13, vs B then C install = 3+4=7 -> 13
assert remaining_post == 13, remaining_post
print(f"case start postponement: remaining={remaining_post}")

# --- 4. everything finished: remaining 0, T is just tau + dummy ---
done = {
    "A": phases("A", 1,
                ("COMPLETED", 1, 2),
                ("COMPLETED", 3, 3),
                ("COMPLETED", 4, 7)),
}
remaining_done = remaining_periods_after_tau(done, 20, D, L, {1: 4}, [], C_install=1)
assert remaining_done == 0, remaining_done
assert horizon_from_remaining(20, 0) == 21
print("case all completed: remaining=0")

# --- 5. small MIP: freeze finished work at tau, leftover must fit in T ---
from planning_tool.model import PrefabScheduler  # noqa: E402

tau = 8
N = 2
I_d = {1: 3, 2: 4}
D = {1: 2, 2: 2}
L = {1: 1, 2: 1}
E = [(1, 2)]
states_mip = {
    "A": phases("A", 1,
                ("COMPLETED", 1, 2),
                ("COMPLETED", 3, 4),
                ("COMPLETED", 4, 6)),
    "B": phases("B", 2,
                ("COMPLETED", 2, 3),
                ("COMPLETED", 4, 5),
                ("NOT_STARTED",)),
}
remaining_mip = remaining_periods_after_tau(states_mip, tau, D, L, I_d, E, C_install=1)
T = horizon_from_remaining(tau, remaining_mip)
print(f"MIP case: remaining={remaining_mip}, T={T}")
assert T > tau
assert remaining_mip == 4  # only B's installation is left

s = PrefabScheduler(
    N=N, T=T, d=dict(I_d), E=E, D=D, L=L,
    C_install=1, M_machine=2, S_site=10, S_fac=10,
    w_duration=0.4, w_transport=0.1, w_site_storage=0.4, w_factory_storage=0.1,
    reference={"ref_duration": 20, "ref_transport": 1,
               "ref_site_storage": 10, "ref_factory_storage": 0},
    min_batch_size=1, max_batch_size=5,
)
s.set_fixed_constraints(
    fixed_installation_starts={1: 4},
    fixed_production_starts={1: 1, 2: 2},
    fixed_arrival_times={1: 4, 2: 5},
    reoptimize_from_time=tau,
)
s.build_model()
s.m.Params.OutputFlag = 0
s.m.Params.TimeLimit = 30
s.m.Params.MIPGap = 0.0
status = s.solve()
assert s.m.SolCount > 0, f"no feasible reopt solution, status={status}"
install_2 = round(sum(t * s.x[2, t].X for t in range(1, T + 1)))
assert install_2 >= tau, install_2
finish = round(sum(t * s.x[s.dummy_end, t].X for t in range(1, T + 1)))
assert finish <= T, finish
print(f"MIP reopt: status={status}, B installs at {install_2}, finish={finish}, T={T}")
print("reopt horizon OK")
