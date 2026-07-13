"""Aggregate authority-hierarchy trials into a per-model authority ranking.

For each model we compute, over all CONFLICT trials:
  * follow-rate(source) = (# conflicts the source WON) / (# conflicts it took part in)
    -- a round-robin win rate; every pair pits two sources head-to-head, so the
    win-rate ordering IS the authority hierarchy (highest = treated as ground truth).
  * a pairwise win matrix (who beats whom, and by how much).
  * a binomial confidence interval per follow-rate: Clopper-Pearson exact (scipy) if
    available, else a Wilson-score interval (clearly labeled).
  * baseline compliance on AGREE trials (sanity: does the agent follow at all?).

Usage:
    python analyze.py [results.jsonl ...]
If no path is given, reads results/authority_results.jsonl.
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from scipy.stats import beta as _beta  # noqa: F401
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


# -------------------------------------------------------------------- intervals
def clopper_pearson(k, n, alpha=0.05):
    """Exact Clopper-Pearson 95% CI for a binomial proportion (needs scipy)."""
    from scipy.stats import beta
    if n == 0:
        return (0.0, 1.0)
    lo = 0.0 if k == 0 else beta.ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - alpha / 2, k + 1, n - k)
    return (float(lo), float(hi))


def wilson(k, n, z=1.959963984540054):
    """Wilson-score 95% CI -- pure-python fallback when scipy is absent."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def ci(k, n):
    if _HAVE_SCIPY:
        return clopper_pearson(k, n), "Clopper-Pearson (exact)"
    return wilson(k, n), "Wilson (scipy absent)"


# -------------------------------------------------------------------- loading
def load(paths):
    if not paths:
        paths = [os.path.join(HERE, "results", "authority_results.jsonl")]
    rows = []
    for p in paths:
        if not os.path.exists(p):
            print(f"[warn] no such results file: {p}", file=sys.stderr)
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return [r for r in rows if r.get("action") != "error"]


# -------------------------------------------------------------------- aggregation
def analyze_model(rows):
    """Return the analysis dict for one model's rows."""
    conflicts = [r for r in rows if r.get("relation") == "conflict"]
    agrees = [r for r in rows if r.get("relation") == "agree"]

    # per-source participation + wins over conflict trials
    part = defaultdict(int)
    wins = defaultdict(int)
    ambiguous = 0
    pair_wins = defaultdict(lambda: defaultdict(int))   # pair_wins[winner][loser]
    for r in conflicts:
        a, b = r["source_a"], r["source_b"]
        part[a] += 1
        part[b] += 1
        w = r["which_won"]
        if w in (a, b):
            wins[w] += 1
            loser = b if w == a else a
            pair_wins[w][loser] += 1
        else:
            ambiguous += 1

    sources = sorted(part, key=lambda s: (-(wins[s] / part[s] if part[s] else 0), s))
    follow = {}
    for s in sources:
        n, k = part[s], wins[s]
        (lo, hi), method = ci(k, n)
        follow[s] = {"won": k, "n": n, "rate": (k / n if n else 0.0),
                     "ci95": [lo, hi], "ci_method": method}

    # baseline compliance on agree trials
    agree_followed = sum(1 for r in agrees if r["which_won"] == "followed")
    agree_n = len(agrees)

    return {
        "n_conflict": len(conflicts),
        "n_agree": agree_n,
        "ambiguous_conflicts": ambiguous,
        "authority_ranking": sources,             # highest authority first
        "follow_rates": follow,
        "pairwise_wins": {w: dict(l) for w, l in pair_wins.items()},
        "agree_compliance": {"followed": agree_followed, "n": agree_n,
                             "rate": (agree_followed / agree_n if agree_n else 0.0)},
    }


def analyze(rows):
    by_model = defaultdict(list)
    for r in rows:
        by_model[r.get("model", "?")].append(r)
    return {m: analyze_model(rs) for m, rs in by_model.items()}


# -------------------------------------------------------------------- reporting
def print_report(report):
    for model, a in report.items():
        print(f"\n================ authority hierarchy: {model} ================")
        print(f"conflict trials: {a['n_conflict']}  |  agree trials: {a['n_agree']}  "
              f"|  ambiguous conflicts: {a['ambiguous_conflicts']}")
        print(f"agree-trial compliance (sanity): "
              f"{a['agree_compliance']['followed']}/{a['agree_compliance']['n']} "
              f"= {a['agree_compliance']['rate']:.2f}")
        print("\nRANK  source                    won/ n   follow-rate  95% CI")
        for i, s in enumerate(a["authority_ranking"], 1):
            f = a["follow_rates"][s]
            lo, hi = f["ci95"]
            print(f" {i:>2}   {s:24} {f['won']:>3}/{f['n']:<3}   "
                  f"{f['rate']:.3f}        [{lo:.3f}, {hi:.3f}]")
        method = next(iter(a["follow_rates"].values()), {}).get("ci_method", "n/a")
        print(f"\nCI method: {method}")
        order = " > ".join(a["authority_ranking"])
        print(f"derived ranking (ground truth first): {order}")


if __name__ == "__main__":
    rows = load(sys.argv[1:])
    if not rows:
        print("no rows to analyze.")
        sys.exit(0)
    report = analyze(rows)
    print_report(report)
