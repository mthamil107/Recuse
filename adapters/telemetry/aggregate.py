#!/usr/bin/env python3
"""Aggregate Recuse telemetry logs into summary counts.

Reads one or more append-only JSON-Lines telemetry logs (see README.md) and
prints emission counts per protocol x directive, plus any observed withdrawals.

Dependency-light: Python standard library only. Runs on Python 3.8+.

The telemetry event schema (recuse.telemetry/v1) is deliberately coarse and
carries NO IPs, hostnames, usernames, resource names, or other PII -- only a
protocol, an advisory directive, a coded outcome, an hour-bucketed timestamp,
and a unit count. This aggregator therefore cannot leak identifying data even
if handed a full log.

Usage:
    python3 aggregate.py FILE [FILE ...]

Non-telemetry lines (anything without schema == "recuse.telemetry/v1") are
ignored, so pointing this at a mixed log (e.g. a Kubernetes stdout capture that
also contains decision records) is safe.
"""

import json
import sys
from collections import defaultdict

SCHEMA = "recuse.telemetry/v1"

# Known enum tokens. Unknown values are folded to a safe placeholder so a
# malformed or hostile log line can never inject arbitrary text into the report.
KNOWN_PROTOCOLS = {"ssh", "postgres", "kubernetes"}
KNOWN_DIRECTIVES = {"deny", "throttle", "warn", "other"}
KNOWN_OUTCOMES = {"emitted", "withdrawn"}


def _coerce(value, allowed, fallback="other"):
    """Return value if it is an allowed token, else the fallback placeholder."""
    return value if value in allowed else fallback


def aggregate(paths):
    """Aggregate telemetry files.

    Returns a dict with:
      - emitted:   {(protocol, directive): count}
      - withdrawn: {(protocol, directive): count}
      - files:     number of files read
      - events:    number of telemetry events counted
      - skipped:   number of non-telemetry / unparseable lines skipped
    """
    emitted = defaultdict(int)
    withdrawn = defaultdict(int)
    events = 0
    skipped = 0
    files = 0

    for path in paths:
        files += 1
        try:
            fh = open(path, "r", encoding="utf-8")
        except OSError as exc:
            print("aggregate: cannot open {}: {}".format(path, exc),
                  file=sys.stderr)
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    skipped += 1
                    continue
                if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
                    skipped += 1
                    continue

                protocol = _coerce(obj.get("protocol"), KNOWN_PROTOCOLS)
                directive = _coerce(obj.get("directive"), KNOWN_DIRECTIVES)
                outcome = _coerce(obj.get("outcome"), KNOWN_OUTCOMES, "emitted")

                # count defaults to 1; ignore non-int / negative values.
                count = obj.get("count", 1)
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    count = 1

                key = (protocol, directive)
                if outcome == "withdrawn":
                    withdrawn[key] += count
                else:
                    emitted[key] += count
                events += 1

    return {
        "emitted": dict(emitted),
        "withdrawn": dict(withdrawn),
        "files": files,
        "events": events,
        "skipped": skipped,
    }


def format_report(result):
    """Render the aggregation result as a human-readable text report."""
    lines = []
    lines.append("Recuse telemetry summary")
    lines.append("=" * 40)
    lines.append("files read:       {}".format(result["files"]))
    lines.append("telemetry events: {}".format(result["events"]))
    lines.append("skipped lines:    {}".format(result["skipped"]))
    lines.append("")

    emitted = result["emitted"]
    lines.append("Signal emissions (protocol x directive):")
    if emitted:
        total = 0
        for (protocol, directive) in sorted(emitted):
            n = emitted[(protocol, directive)]
            total += n
            lines.append("  {:<12} {:<10} {}".format(protocol, directive, n))
        lines.append("  {:<12} {:<10} {}".format("TOTAL", "", total))
    else:
        lines.append("  (none)")
    lines.append("")

    withdrawn = result["withdrawn"]
    lines.append("Observed withdrawals (protocol x directive):")
    if withdrawn:
        total_w = 0
        for (protocol, directive) in sorted(withdrawn):
            n = withdrawn[(protocol, directive)]
            total_w += n
            lines.append("  {:<12} {:<10} {}".format(protocol, directive, n))
        lines.append("  {:<12} {:<10} {}".format("TOTAL", "", total_w))
    else:
        lines.append("  (none observed)")

    return "\n".join(lines)


def main(argv):
    if len(argv) < 2:
        print("usage: python3 aggregate.py FILE [FILE ...]", file=sys.stderr)
        return 2
    result = aggregate(argv[1:])
    print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
