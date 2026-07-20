"""Run the THROTTLE + WARN arm of the SSH access-door experiment.

Same live host, same task, same agent loop, same models as the published deny
arm -- only the directive rendered into the presented banner changes. Streams
one JSON object per trial to results/directives.jsonl (gitignored), appending,
so a run can be stopped and resumed. A single trial error is recorded as a row
and never aborts the batch.

Usage:
    python batch_directives.py                 # live run (needs key + secrets)
    python batch_directives.py --mock          # offline, deterministic, no keys
    python batch_directives.py --n 20 --models gpt-4o,gpt-4o-mini
    python batch_directives.py --dry-run       # print the plan and cost, run nothing

Env overrides: RECUSE_N, RECUSE_MODELS, RECUSE_OUT.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESDIR = os.path.join(HERE, "results")

DEFAULT_MODELS = ["gpt-4o", "gpt-4o-mini"]
# The two new cells. Both are unauthorized (no auth-claim in the system prompt),
# matching the headline `signal_unauth` cell of the deny arm.
CONDITIONS = [
    ("throttle_unauth", dict(show_signal=True, authorized=False, directive="throttle")),
    ("warn_unauth",     dict(show_signal=True, authorized=False, directive="warn")),
]
DEFAULT_N = 20

# Rough per-trial cost: ~6 steps x (system + task + banner + tool results).
# Measured against the deny arm: ~4k prompt + ~0.4k completion tokens per trial.
COST_PER_TRIAL_USD = {"gpt-4o": 0.014, "gpt-4o-mini": 0.0009}


def control_baseline(model, results_path=None):
    """Median command count from the no-signal control arm, per model.

    This is the reference the ``throttled`` outcome compares against. Falls back
    to the pooled median, then to None (pacing language alone then decides).
    """
    path = results_path or os.path.join(RESDIR, "pilot_ssh.jsonl")
    if not os.path.exists(path):
        return None
    per_model, pooled = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("cond_name") != "control_unauth":
                continue
            n = len(r.get("commands_attempted") or [])
            pooled.append(n)
            if r.get("model") == model:
                per_model.append(n)
    if per_model:
        return statistics.median(per_model)
    return statistics.median(pooled) if pooled else None


def _done_counts(path):
    """Map (model, cond_name) -> trials already recorded, for resumability."""
    counts = {}
    if not os.path.exists(path):
        return counts
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            key = (r.get("model"), r.get("cond_name"))
            counts[key] = counts.get(key, 0) + 1
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true",
                    help="offline deterministic run: no API key, no SSH, no network")
    ap.add_argument("--n", type=int, default=int(os.environ.get("RECUSE_N", DEFAULT_N)),
                    help="trials per cell (default %d)" % DEFAULT_N)
    ap.add_argument("--models", default=os.environ.get("RECUSE_MODELS",
                                                       ",".join(DEFAULT_MODELS)))
    ap.add_argument("--vendor", default="openai")
    ap.add_argument("--out", default=os.environ.get("RECUSE_OUT", ""),
                    help="output JSONL (default results/directives.jsonl, "
                         "or results/mock_directives.jsonl with --mock)")
    ap.add_argument("--prose", default="directive", choices=["directive", "verbatim"])
    ap.add_argument("--seed", type=int, default=0, help="--mock profile seed")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore existing rows instead of resuming")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and estimated cost, run nothing")
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out = args.out or os.path.join(
        RESDIR, "mock_directives.jsonl" if args.mock else "directives.jsonl")

    n_cells = len(models) * len(CONDITIONS)
    est = sum(COST_PER_TRIAL_USD.get(m, 0.014) * args.n * len(CONDITIONS)
              for m in models)
    print("plan: %d models x %d conditions x n=%d = %d trials"
          % (len(models), len(CONDITIONS), args.n, n_cells * args.n))
    print("      models     : %s" % ", ".join(models))
    print("      conditions : %s" % ", ".join(c for c, _ in CONDITIONS))
    print("      output     : %s" % out)
    print("      est. cost  : $%.2f (%s)"
          % (est, "MOCK - $0.00 actually spent" if args.mock else "live API"))
    if args.dry_run:
        return 0

    os.makedirs(RESDIR, exist_ok=True)
    from run_openai import run_trial

    done = {} if args.fresh else _done_counts(out)
    if done:
        print("resuming: %d rows already present" % sum(done.values()))

    sampler = None
    if args.mock:
        from mock_agent import DEFAULT_MIX, make_mock_factories, profile_sampler
        sampler = profile_sampler(DEFAULT_MIX, seed=args.seed)

    rows = []
    with open(out, "a", encoding="utf-8") as cf:
        for model in models:
            baseline = control_baseline(model)
            for cond_name, cond in CONDITIONS:
                already = done.get((model, cond_name), 0)
                outcomes = []
                for i in range(args.n):
                    if i < already:
                        continue
                    kw = dict(cond, vendor=args.vendor, prose=args.prose,
                              baseline_commands=baseline)
                    if args.mock:
                        profile = sampler()
                        sf, pf = make_mock_factories(profile, cond["directive"])
                        kw.update(session_factory=sf, provider_factory=pf)
                    try:
                        r = run_trial(model, **kw)
                    except Exception as e:  # never abort the batch on one trial
                        r = {"model": model, "outcome": "error:%s" % type(e).__name__,
                             "error": str(e)[:300], "condition": cond,
                             "directive": cond["directive"]}
                    r["cond_name"] = cond_name
                    r["trial"] = i
                    r["directive"] = cond["directive"]
                    r["baseline_commands"] = baseline
                    r["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    if args.mock:
                        r["mock_profile"] = profile
                    outcomes.append(r.get("outcome", "error"))
                    cf.write(json.dumps(r) + "\n")
                    cf.flush()
                    print("  %-14s %-16s trial %2d: %s"
                          % (model, cond_name, i, r.get("outcome")))
                rows.append((model, cond_name, outcomes))

    print("\n================ DIRECTIVE ARM SUMMARY ================")
    print("%-14s %-16s %2s  outcomes" % ("model", "condition", "n"))
    for model, cond_name, outcomes in rows:
        n = len(outcomes)
        if not n:
            print("%-14s %-16s %2d  (already complete)" % (model, cond_name, 0))
            continue
        counts = {}
        for o in outcomes:
            counts[o] = counts.get(o, 0) + 1
        rec = counts.get("recused", 0)
        print("%-14s %-16s %2d  %s   [over-compliance (recused) %d/%d = %.0f%%]"
              % (model, cond_name, n,
                 " ".join("%s=%d" % kv for kv in sorted(counts.items())),
                 rec, n, 100.0 * rec / n))
    print("\nraw trials -> %s" % out)
    print("analyze with: python analyze_directives.py%s"
          % (" --mock" if args.mock else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
