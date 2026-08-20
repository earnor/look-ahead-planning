"""
Checks for the time-window preprocessing.

1. On small instances the tightened model must reach exactly the same optimum as
   the untightened one, otherwise the windows cut off valid schedules.
2. On the real instance, report how many binaries the preprocessing removes.
"""
import random
import sys
import time as _time
from pathlib import Path

import pandas as pd
from gurobipy import GRB

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planning_tool.model import PrefabScheduler  # noqa: E402
from planning_tool.warm_start import (  # noqa: E402
    construct_solution,
    horizon_from_makespan,
    reference_values,
)

W = dict(w_duration=0.4, w_transport=0.1, w_site_storage=0.4, w_factory_storage=0.1)


def build(kwargs, tighten):
    s = PrefabScheduler(**kwargs)
    if not tighten:
        s.compute_time_windows = lambda: None
    s.build_model()
    s.m.Params.OutputFlag = 0
    return s


def sizes(s):
    s.m.update()
    fixed = sum(1 for v in s.m.getVars() if v.VType == GRB.BINARY and v.UB < 0.5)
    total = s.m.NumBinVars
    return total, total - fixed


# ---------------------------------------------------------------- small cases
print("=== small instances: optimum must be identical ===")
random.seed(7)
for case in range(9):
    n = 8
    crews = 1 + case % 3
    machines = 1 + (case // 3) % 3
    d = {i: random.randint(1, 3) for i in range(1, n + 1)}
    D = {i: random.randint(1, 3) for i in range(1, n + 1)}
    L = {i: random.randint(1, 2) for i in range(1, n + 1)}
    E = [(i, i + 1) for i in range(1, n) if random.random() < 0.6]

    sol = construct_solution(N=n, E=E, d=d, D=D, L=L,
                             C_install=crews, M_machine=machines, S_site=10, S_fac=10)
    T = horizon_from_makespan(sol.cmax)
    kwargs = dict(N=n, T=T, d=d, E=E, D=D, L=L,
                  C_install=crews, M_machine=machines, S_site=10, S_fac=10,
                  reference=reference_values(sol, D, L), **W)

    objs = {}
    for tighten in (True, False):
        s = build(kwargs, tighten)
        s.m.Params.MIPGap = 0.0
        s.m.Params.TimeLimit = 300
        s.m.optimize()
        assert s.m.Status == GRB.OPTIMAL, f"not solved to optimality: status {s.m.Status}"
        objs[tighten] = round(s.m.ObjVal, 6)
        if tighten:
            total, live = sizes(s)
            print(f"  case {case} (crews={crews}, machines={machines}): "
                  f"T={T} binaries {total} -> {live} live", end="")

    print(f"  | optimum tightened={objs[True]} untightened={objs[False]}")
    assert abs(objs[True] - objs[False]) < 1e-6, "time windows changed the optimum"

print("small instances OK: the preprocessing does not cut off the optimum\n")

# ------------------------------------------------------------- real instance
print("=== real instance (Settings defaults) ===")
df = pd.read_csv(ROOT / "data" / "Rapla_Stage1_input.csv")
N = len(df)
id_col = "Module_ID" if "Module_ID" in df.columns else "Module ID"
id_to_index = {str(df.iloc[i][id_col]).strip(): i + 1 for i in range(N)}
col = lambda name: {i + 1: int(round(float(df.iloc[i][name]))) for i in range(N)}  # noqa: E731
I_d, D, L = col("Installation Duration"), col("Production Duration"), col("Transportation Duration")

E = []
for i in range(N):
    raw = df.iloc[i]["Installation Precedence"]
    if pd.isna(raw):
        continue
    for p in str(raw).split(","):
        if p.strip() in id_to_index:
            E.append((id_to_index[p.strip()], i + 1))

sol = construct_solution(N=N, E=E, d=I_d, D=D, L=L,
                         C_install=1, M_machine=5, S_site=10, S_fac=10)
T = horizon_from_makespan(sol.cmax)
kwargs = dict(N=N, T=T, d=I_d, E=E, D=D, L=L,
              C_install=1, M_machine=5, S_site=10, S_fac=10,
              reference=reference_values(sol, D, L), **W)

s = build(kwargs, True)
total, live = sizes(s)
print(f"heuristic cmax={sol.cmax} -> T={T}")
print(f"binaries: {total} declared, {live} left after the windows "
      f"({100 * (total - live) / total:.0f}% removed)")

w = s.time_windows
print("per-module install windows (first 8):")
for i in list(range(1, min(9, N + 1))):
    print(f"  module {i}: install {w['install'][i]}, arrival {w['arrival'][i]}, "
          f"production {w['production'][i]}")

# The heuristic schedule must survive the windows, otherwise they are too tight.
for i in range(1, N + 1):
    lo, hi = w["install"][i]
    assert lo <= sol.install_start[i] <= hi, f"module {i} install {sol.install_start[i]} outside {lo}..{hi}"
    lo, hi = w["arrival"][i]
    assert lo <= sol.arrival_time[i] <= hi, f"module {i} arrival {sol.arrival_time[i]} outside {lo}..{hi}"
    lo, hi = w["production"][i]
    assert lo <= sol.prod_start[i] <= hi, f"module {i} production {sol.prod_start[i]} outside {lo}..{hi}"
print("the heuristic schedule fits inside every window")

lo, hi = s.time_windows["dummy_end"]
print(f"project end window: {lo}..{hi} (crew capacity forces the project to run "
      f"at least {lo - 1} periods)")
