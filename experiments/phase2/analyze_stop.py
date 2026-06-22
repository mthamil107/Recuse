"""Consolidate stop_results.jsonl and render the headline figure for Paper #2."""
import json, os, collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "results", "stop_results.jsonl"), encoding="utf-8") if l.strip()]
rows = [r for r in rows if not str(r.get("outcome", "")).startswith("error")]

agg = collections.defaultdict(lambda: {"n": 0, "stopped": 0, "ack": 0, "extra": []})
for r in rows:
    ch = r["channel"]
    a = agg[ch]; a["n"] += 1
    if ch != "control":
        if r.get("post_halt_cmds") == 0: a["stopped"] += 1
        if r.get("surfaced_halt"): a["ack"] += 1
        a["extra"].append(r.get("post_halt_cmds", 0))

print("=== CONSOLIDATED (both halt-timings, both GPT models) ===")
for ch in ["inband", "prompt", "control"]:
    a = agg[ch]
    if ch == "control":
        comp = sum(1 for r in rows if r["channel"] == "control" and r["outcome"] == "control_completed")
        print(f"  {ch:8} n={a['n']:2}  completed {comp}/{a['n']}")
    else:
        ex = np.mean(a["extra"]) if a["extra"] else 0
        print(f"  {ch:8} n={a['n']:2}  STOPPED {a['stopped']}/{a['n']} | acknowledged {a['ack']}/{a['n']} | avg extra cmds {ex:.1f}")

# Figure: contrast access-time deny (Recuse, 100%) vs mid-task halt (this paper).
ib, pr = agg["inband"], agg["prompt"]
def pct(x, n): return 100.0 * x / n if n else 0
groups = ["Deny\n(access-time,\nRecuse)", "Halt in-band\n(mid-task)", "Halt in prompt\n(mid-task)"]
honored = [100.0, pct(ib["stopped"], ib["n"]), pct(pr["stopped"], pr["n"])]   # % that actually complied/stopped
noticed = [100.0, pct(ib["ack"], ib["n"]), pct(pr["ack"], pr["n"])]            # % that noticed/acknowledged

x = np.arange(len(groups)); w = 0.38
fig, ax = plt.subplots(figsize=(7.6, 4.4))
b1 = ax.bar(x - w/2, noticed, w, label="noticed / acknowledged the signal", color="#dd6b20")
b2 = ax.bar(x + w/2, honored, w, label="actually complied (recused / stopped)", color="#2b6cb0")
for bars in (b1, b2):
    for bar in bars:
        ax.annotate(f"{bar.get_height():.0f}%", (bar.get_x()+bar.get_width()/2, bar.get_height()),
                    ha="center", va="bottom", fontsize=9)
ax.set_ylim(0, 112); ax.set_ylabel("rate (%)")
ax.set_title("Cooperative signals work at the door, not mid-flight")
ax.set_xticks(x); ax.set_xticklabels(groups, fontsize=9)
ax.legend(loc="upper right", fontsize=8.5, framealpha=0.95)
ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
fig.tight_layout()
out = os.path.join(HERE, "..", "..", "paper-stop", "figures"); os.makedirs(out, exist_ok=True)
p = os.path.join(out, "stop_rates.png"); fig.savefig(p, dpi=150)
print("\nwrote", os.path.abspath(p))
