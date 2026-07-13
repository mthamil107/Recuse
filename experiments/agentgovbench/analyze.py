"""AgentGovBench analysis.

Reads per-trial JSONL and computes, per cell (agent x protocol x directive x
condition), the recusal-compliance rate with an EXACT-binomial
(Clopper-Pearson) confidence interval.

CI method resolution:
  1. scipy.stats.beta.ppf if scipy is importable (exact Clopper-Pearson).
  2. else a self-contained pure-Python Clopper-Pearson via the beta-quantile
     (regularized incomplete beta, inverted by bisection). Still EXACT.
  3. Wilson score interval is also implemented and used only if explicitly
     requested (--wilson) or if the exact path fails; the emitted method label
     always says which interval produced each number.

Outputs:
  * results_table.csv   — one row per cell.
  * results_table.txt   — pretty aligned table.
  * leaderboard.json    — agents ranked by compliance, per directive
                          (consumed by leaderboard/index.html).

Compliance metric (per cell): among DECIDABLE trials (outcome in
{recused, proceeded}), rate = recused / (recused + proceeded). Trials coded
no_signal_seen / error are reported but excluded from the rate denominator.
Control cells report a completion rate instead.
"""
import argparse
import csv
import json
import math
import os
import sys
import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ beta math
def _gammaln(x):
    # Lanczos approximation.
    g = [676.5203681218851, -1259.1392167224028, 771.32342877765313,
         -176.61502916214059, 12.507343278686905, -0.13857109526572012,
         9.9843695780195716e-6, 1.5056327351493116e-7]
    if x < 0.5:
        return math.log(math.pi / math.sin(math.pi * x)) - _gammaln(1 - x)
    x -= 1
    a = 0.99999999999980993
    t = x + 7.5
    for i, c in enumerate(g):
        a += c / (x + i + 1)
    return 0.5 * math.log(2 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _gammaln(a + b) - _gammaln(a) - _gammaln(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _beta_ppf(p, a, b):
    """Inverse of I_x(a,b)=p by bisection on [0,1]."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _betai(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


# scipy path if available.
try:
    from scipy.stats import beta as _scipy_beta  # type: ignore
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


def _ppf(p, a, b):
    if _HAVE_SCIPY:
        return float(_scipy_beta.ppf(p, a, b))
    return _beta_ppf(p, a, b)


CI_METHOD = ("clopper-pearson-exact (scipy.stats.beta)" if _HAVE_SCIPY
             else "clopper-pearson-exact (pure-python incomplete-beta)")


def clopper_pearson(k, n, conf=0.95):
    """Exact binomial CI. Returns (rate, lo, hi)."""
    if n == 0:
        return (float("nan"), 0.0, 1.0)
    alpha = 1.0 - conf
    lo = 0.0 if k == 0 else _ppf(alpha / 2.0, k, n - k + 1)
    hi = 1.0 if k == n else _ppf(1.0 - alpha / 2.0, k + 1, n - k)
    return (k / n, lo, hi)


def wilson(k, n, conf=0.95):
    """Wilson score interval (labeled fallback)."""
    if n == 0:
        return (float("nan"), 0.0, 1.0)
    # z for two-sided conf; 1.959964 at 95%.
    z = 1.959963984540054 if abs(conf - 0.95) < 1e-9 else _z_from_conf(conf)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def _z_from_conf(conf):
    # inverse normal via rational approx (Acklam) — only used for non-95% Wilson.
    p = 1 - (1 - conf) / 2
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ------------------------------------------------------------------ aggregation
def load_rows(paths):
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def cell_stats(rows, conf, method):
    """Aggregate rows by (agent, protocol, directive, condition)."""
    ci = wilson if method == "wilson" else clopper_pearson
    cells = {}
    for r in rows:
        key = (r["agent_id"], r["protocol"], r["directive"], r["condition"])
        c = cells.setdefault(key, {"recused": 0, "proceeded": 0,
                                   "no_signal_seen": 0, "control_completed": 0,
                                   "control_no_action": 0, "error": 0,
                                   "surfaced": 0, "partial": 0, "total": 0})
        c["total"] += 1
        o = r.get("outcome", "")
        if o and o.startswith("error"):
            c["error"] += 1
        elif o in c:
            c[o] += 1
        if r.get("surfaced_notice"):
            c["surfaced"] += 1
        if r.get("partial_candidate"):
            c["partial"] += 1

    out = []
    for (agent, proto, directive, cond), c in sorted(cells.items()):
        if cond == "control":
            k, n = c["control_completed"], c["control_completed"] + c["control_no_action"]
            metric = "completion"
        else:
            k, n = c["recused"], c["recused"] + c["proceeded"]
            metric = "compliance"
        rate, lo, hi = ci(k, n, conf)
        out.append({
            "agent_id": agent, "protocol": proto, "directive": directive,
            "condition": cond, "metric": metric,
            "k": k, "n": n, "total": c["total"],
            "recused": c["recused"], "proceeded": c["proceeded"],
            "no_signal_seen": c["no_signal_seen"], "error": c["error"],
            "surfaced": c["surfaced"], "partial_candidate": c["partial"],
            "rate": rate, "ci_lo": lo, "ci_hi": hi,
        })
    return out


def leaderboard(rows, conf, method):
    """Per-directive agent ranking, aggregating across protocols, for the
    signal and signal_auth conditions."""
    ci = wilson if method == "wilson" else clopper_pearson
    agg = {}  # (directive, agent, condition) -> [k, n]
    for r in rows:
        if r["condition"] == "control":
            continue
        o = r.get("outcome", "")
        if o not in ("recused", "proceeded"):
            continue
        key = (r["directive"], r["agent_id"], r["condition"])
        a = agg.setdefault(key, [0, 0])
        a[1] += 1
        if o == "recused":
            a[0] += 1

    directives = {}
    all_dirs = sorted({d for (d, _, _) in agg})
    all_agents = sorted({a for (_, a, _) in agg})
    for d in all_dirs:
        entries = []
        for agent in all_agents:
            row = {"agent_id": agent}
            for cond in ("signal", "signal_auth"):
                k, n = agg.get((d, agent, cond), [0, 0])
                rate, lo, hi = ci(k, n, conf) if n else (None, None, None)
                row[cond] = {"k": k, "n": n, "rate": rate, "ci_lo": lo, "ci_hi": hi}
            entries.append(row)
        entries.sort(key=lambda e: (e["signal"]["rate"] if e["signal"]["rate"]
                                    is not None else -1), reverse=True)
        directives[d] = entries
    return directives


# ------------------------------------------------------------------ emit
def write_csv(cells, path):
    fields = ["agent_id", "protocol", "directive", "condition", "metric",
              "k", "n", "total", "recused", "proceeded", "no_signal_seen",
              "error", "surfaced", "partial_candidate", "rate", "ci_lo", "ci_hi"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in cells:
            w.writerow(c)


def write_txt(cells, path, conf, method):
    lines = [f"AgentGovBench results  |  CI: {method}  |  conf={conf:.0%}", ""]
    hdr = (f"{'agent':18} {'proto':11} {'dir':9} {'cond':12} {'metric':10} "
           f"{'k/n':>8} {'rate':>6} {'95% CI':>16}  surf")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for c in cells:
        rate = "  n/a" if c["n"] == 0 else f"{c['rate']*100:5.1f}"
        ci = "     n/a" if c["n"] == 0 else f"[{c['ci_lo']*100:4.1f},{c['ci_hi']*100:5.1f}]"
        lines.append(
            f"{c['agent_id']:18} {c['protocol']:11} {c['directive']:9} "
            f"{c['condition']:12} {c['metric']:10} "
            f"{str(c['k'])+'/'+str(c['n']):>8} {rate:>6} {ci:>16}  {c['surfaced']}")
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description="Analyze AgentGovBench JSONL")
    ap.add_argument("inputs", nargs="*",
                    help="per-trial JSONL file(s). Default: results/*.jsonl")
    ap.add_argument("--conf", type=float, default=0.95)
    ap.add_argument("--wilson", action="store_true",
                    help="use the Wilson score interval instead of exact CP")
    ap.add_argument("--outdir", default=HERE)
    args = ap.parse_args(argv or sys.argv[1:])

    inputs = args.inputs
    if not inputs:
        rdir = os.path.join(HERE, "results")
        inputs = [os.path.join(rdir, f) for f in os.listdir(rdir)
                  if f.endswith(".jsonl")] if os.path.isdir(rdir) else []
    if not inputs:
        print("no input JSONL found (pass a path or populate results/).")
        return 1

    method = "wilson" if args.wilson else "clopper-pearson"
    method_label = "wilson-score" if args.wilson else CI_METHOD

    rows = load_rows(inputs)
    print(f"loaded {len(rows)} trials from {len(inputs)} file(s)")
    print(f"CI method: {method_label}")

    cells = cell_stats(rows, args.conf, method)
    lb = leaderboard(rows, args.conf, method)

    csv_path = os.path.join(args.outdir, "results_table.csv")
    txt_path = os.path.join(args.outdir, "results_table.txt")
    lb_path = os.path.join(args.outdir, "leaderboard", "leaderboard.json")
    os.makedirs(os.path.dirname(lb_path), exist_ok=True)

    write_csv(cells, csv_path)
    text = write_txt(cells, txt_path, args.conf, method_label)
    with open(lb_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "ci_method": method_label,
            "conf": args.conf,
            "n_trials": len(rows),
            "directives": lb,
            "cells": cells,
        }, f, indent=2)

    print("\n" + text)
    print(f"wrote:\n  {csv_path}\n  {txt_path}\n  {lb_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
