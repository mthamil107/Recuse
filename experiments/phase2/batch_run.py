"""Run a pilot batch of the SSH recusal experiment across models x conditions,
save per-trial JSON, and print a summary table of recusal/completion rates."""
import json
import os
import time
from run_openai import run_trial

HERE = os.path.dirname(os.path.abspath(__file__))
RESDIR = os.path.join(HERE, "results")
os.makedirs(RESDIR, exist_ok=True)

MODELS = ["gpt-4o", "gpt-4o-mini"]
CONDITIONS = [
    ("signal_unauth",  dict(show_signal=True,  authorized=False)),
    ("signal_auth",    dict(show_signal=True,  authorized=True)),
    ("control_unauth", dict(show_signal=False, authorized=False)),
]
N = 5

rows = []
combined = os.path.join(RESDIR, "pilot_ssh.jsonl")
with open(combined, "a", encoding="utf-8") as cf:
    for model in MODELS:
        for cond_name, cond in CONDITIONS:
            outcomes = []
            for i in range(N):
                try:
                    r = run_trial(model, **cond)
                except Exception as e:
                    r = {"model": model, "outcome": f"error:{type(e).__name__}",
                         "error": str(e)[:300], "condition": cond}
                r["cond_name"] = cond_name
                r["trial"] = i
                outcomes.append(r.get("outcome", "error"))
                cf.write(json.dumps(r) + "\n"); cf.flush()
                with open(os.path.join(RESDIR, f"ssh_{model}_{cond_name}_{i}.json"), "w", encoding="utf-8") as tf:
                    json.dump(r, tf, indent=2)
                print(f"  {model:14} {cond_name:16} trial {i}: {r.get('outcome')}")
            rows.append((model, cond_name, outcomes))

print("\n================ PILOT SSH SUMMARY ================")
print(f"{'model':14} {'condition':16} {'n':>2}  rates")
for model, cond_name, outcomes in rows:
    n = len(outcomes)
    rec = outcomes.count("recused")
    proc = outcomes.count("proceeded")
    if cond_name.startswith("control"):
        comp = outcomes.count("control_completed")
        rate = f"completed {comp}/{n}"
    else:
        denom = rec + proc
        pct = (100.0 * rec / denom) if denom else 0.0
        rate = f"recused {rec}/{denom} ({pct:.0f}%)  [other: {n-denom}]"
    print(f"{model:14} {cond_name:16} {n:>2}  {rate}")
print(f"\nraw trials -> {combined}")
