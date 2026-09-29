import csv, json, statistics as S
from pathlib import Path
r = list(csv.DictReader(open(Path(__file__).resolve().parents[2] / "sessions" / "bench" / "bench_biphasic.csv")))
J = lambda x, k: json.loads(x[k]) if x.get(k) not in (None, "", "None") else None
print("rows", len(r), "faulted", {x["faulted"] for x in r}, "fault", {x["fault_reason"] for x in r})
steps = []
for x in r:
    if not steps or steps[-1][0] != x["step"]:
        steps.append((x["step"], []))
    steps[-1][1].append(x)
for name, rows in steps:
    tail = rows[len(rows) // 2:]          # settled half
    pk = [J(x, "peak") for x in tail if J(x, "peak")]
    rms = [J(x, "rms") for x in tail if J(x, "rms")]
    zo = J(tail[-1], "output_impedance")
    q = [J(x, "teleplot").get("bp_qnet_a_uC") for x in tail]
    fr = [float(x["actual_pulse_frequency"]) for x in tail if x["actual_pulse_frequency"] not in ("", "None")]
    m = lambda L, i: round(S.mean(v[i] for v in L) * 1000, 2)
    print(f"step {name:7s} n={len(rows):3d} peak mA A {m(pk,0)} B {m(pk,1)} C {m(pk,2)} D {m(pk,3)} | "
          f"rms mA A {m(rms,0)} B {m(rms,1)} | Zout {[round(v,1) for v in zo]} | qnet_a uC {min(q):.1f}..{max(q):.1f} "
          f"| rate {S.mean(fr):.1f}")
# first rows of the final 12 step: overshoot check
last = steps[-1][1]
print("final-12 first rows peak A/B mA:", [[round(v * 1000, 1) for v in J(x, "peak")[:2]] for x in last[:8]])
print("steady 12 peak A/B:", [round(S.mean(J(x, 'peak')[i] for x in steps[0][1][len(steps[0][1])//2:]) * 1000, 1) for i in (0, 1)])
