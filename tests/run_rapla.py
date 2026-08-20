"""
Single configuration run on Rapla Stage 1: 1 installation crew, 5 machines,
storage 10 on both sides, Settings default costs.

Solves the same instance twice, changing only the horizon, so the effect of the
heuristic-derived T can be read off directly.
"""
import sys
import time as _time
from collections import Counter
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

TIME_CAP = 600

C_install, M_machine, S_site, S_fac = 1, 5, 10, 10
W = dict(w_duration=0.4, w_transport=0.1, w_site_storage=0.4, w_factory_storage=0.1)

df = pd.read_csv(ROOT / "data" / "Rapla_Stage1_input.csv")
N = len(df)
id_col = "Module_ID" if "Module_ID" in df.columns else "Module ID"
id_to_index = {str(df.iloc[i][id_col]).strip(): i + 1 for i in range(N)}


def col(name):
    return {i + 1: int(round(float(df.iloc[i][name]))) for i in range(N)}


I_d, D, L = col("Installation Duration"), col("Production Duration"), col("Transportation Duration")

E = []
for i in range(N):
    raw = df.iloc[i]["Installation Precedence"]
    if pd.isna(raw):
        continue
    for p in str(raw).split(","):
        if p.strip() in id_to_index:
            E.append((id_to_index[p.strip()], i + 1))

print(f"Rapla Stage 1: N={N} modules, {len(E)} precedence arcs")
print(f"crews={C_install}, machines={M_machine}, site storage={S_site}, factory storage={S_fac}")
print(f"durations: install {sum(I_d.values())}, production {sum(D.values())}, transport {sum(L.values())} (totals)\n")

t0 = _time.time()
sol = construct_solution(N=N, E=E, d=I_d, D=D, L=L,
                         C_install=C_install, M_machine=M_machine, S_site=S_site, S_fac=S_fac)
heur_time = _time.time() - t0
T_tight = horizon_from_makespan(sol.cmax)
reference = reference_values(sol, D, L)
print(f"heuristic: makespan {sol.cmax} in {heur_time:.3f}s -> T = {T_tight}")
print(f"heuristic deliveries: {sorted(Counter(sol.arrival_time.values()).items())}")
print(f"objective reference: {reference}\n")


def report(label, T):
    s = PrefabScheduler(N=N, T=T, d=I_d, E=E, D=D, L=L,
                        C_install=C_install, M_machine=M_machine,
                        S_site=S_site, S_fac=S_fac,
                        reference=reference, **W)
    s.build_model()
    s.m.Params.OutputFlag = 0
    s.m.Params.TimeLimit = TIME_CAP
    s.m.update()

    declared = s.m.NumBinVars
    live = declared - sum(1 for v in s.m.getVars() if v.VType == GRB.BINARY and v.UB < 0.5)

    t0 = _time.time()
    s.m.optimize()
    elapsed = _time.time() - t0

    print(f"--- {label} (T={T}) ---")
    print(f"  binaries: {declared} declared, {live} live after time windows "
          f"({100 * (declared - live) / declared:.0f}% fixed to zero)")
    print(f"  rows: {s.m.NumConstrs}")
    print(f"  solve: {elapsed:.0f}s, status {s.m.Status}"
          f"{' (proved 20% gap)' if s.m.Status == GRB.OPTIMAL else f' (hit the {TIME_CAP}s cap)'}")
    if s.m.SolCount == 0:
        print("  no feasible solution found\n")
        return
    print(f"  objective {s.m.ObjVal:.2f}, bound {s.m.ObjBound:.2f}, gap {s.m.MIPGap:.1%}")

    install = {i: sum(t * s.x[i, t].X for t in range(1, T + 1)) for i in range(1, N + 1)}
    arrival = {i: round(sum(t * s.p[i, t].X for t in range(1, T + 1))) for i in range(1, N + 1)}
    makespan = round(sum(t * s.x[s.dummy_end, t].X for t in range(1, T + 1)))
    loads = sorted(Counter(arrival.values()).items())
    deliveries = sum(s.z[t].X for t in range(1, T + 1))
    factory_storage = sum(s.F[t].X for t in range(1, T + 1))
    site_storage = sum(s.I[i, t].X for i in range(1, N + 1) for t in range(1, T + 1))

    ref_storage = max(1.0, reference["ref_site_storage"] + reference["ref_factory_storage"])
    terms = {
        "duration": (W["w_duration"], makespan, reference["ref_duration"]),
        "transport": (W["w_transport"], deliveries, reference["ref_transport"]),
        "site storage": (W["w_site_storage"], site_storage, ref_storage),
        "factory storage": (W["w_factory_storage"], factory_storage, ref_storage),
    }
    print(f"  makespan {makespan}, last installation finishes "
          f"{round(max(install[i] + I_d[i] - 1 for i in range(1, N + 1)))}")
    print(f"  deliveries {len(loads)}: {loads}")
    for name, (w, raw, ref) in terms.items():
        print(f"    {name:<16} raw {raw:>8.1f} / ref {ref:>6.1f} = {raw / ref:5.2f} "
              f"x weight {w} -> {w * raw / ref:.3f}")
    print()


report("heuristic horizon", T_tight)
if "--with-loose" in sys.argv:
    report("loose horizon (what a distant target date would give)", 3 * T_tight)
