"""A tiny CLI for the recuse package.

Commands::

    recuse parse "<text>"   Detect a RECUSE signal in TEXT; print the directive/JSON.
    recuse check <file>     Scan a file for a RECUSE signal; exit non-zero on a stop.
    recuse build <directive> [--reason ...] [--ref ...] [--id ...] [--scope ...]
                            Build and print a sentinel line.
    recuse hook             Claude Code hook: read a hook event as JSON on stdin,
                            block the tool call if it carries a RECUSE stop signal.
    recuse version          Print the package version.

Runnable as ``recuse ...`` (console script) or ``python -m recuse.cli ...``.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .signal import build_signal, parse_signal, scan_text


def _print_signal(signal, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(signal.to_dict(), indent=2))
    else:
        print(f"directive : {signal.directive}")
        print(f"version   : {signal.version_str}")
        print(f"is_stop   : {signal.is_stop}")
        print(f"malformed : {signal.malformed}")
        if signal.params:
            print("params    :")
            for k, v in signal.params.items():
                print(f"    {k} = {v}")


def _cmd_parse(args) -> int:
    signal = parse_signal(args.text, fail_closed=not args.no_fail_closed)
    if signal is None:
        if args.json:
            print(json.dumps(None))
        else:
            print("no RECUSE signal detected")
        return 0
    _print_signal(signal, as_json=args.json)
    return 0


def _cmd_check(args) -> int:
    try:
        with open(args.file, "r", encoding="utf-8", errors="replace") as fh:
            blob = fh.read()
    except OSError as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    signals = scan_text(blob, fail_closed=not args.no_fail_closed)
    if not signals:
        if args.json:
            print(json.dumps([]))
        else:
            print("no RECUSE signal detected")
        return 0
    if args.json:
        print(json.dumps([s.to_dict() for s in signals], indent=2))
    else:
        for s in signals:
            print(f"{s.directive or '<malformed>'}  (is_stop={s.is_stop})  {s.raw}")
    # Exit non-zero if any signal would stop a running agent, so scripts can gate on it.
    return 1 if any(s.is_stop for s in signals) else 0


def _cmd_build(args) -> int:
    try:
        line = build_signal(
            args.directive,
            reason=args.reason,
            scope=args.scope,
            ref=args.ref,
            id=args.id,
            version=args.recuse_version,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(line)
    return 0


def _cmd_hook(args) -> int:
    # Delegate to the hook entry point, which owns the stdin/stdout JSON contract
    # and the Claude Code exit-code convention (0 allow / 2 block).
    from .hooks import main as hook_main

    argv = []
    if args.input:
        argv += ["--input", args.input]
    if args.fail_open:
        argv.append("--fail-open")
    if args.exit_zero:
        argv.append("--exit-zero")
    return hook_main(argv)


def _cmd_version(args) -> int:
    print(f"recuse {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recuse",
        description="Parse, check, and build RECUSE signals.",
    )
    parser.add_argument("--version", action="version",
                        version=f"recuse {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_parse = sub.add_parser("parse", help="detect a RECUSE signal in TEXT")
    p_parse.add_argument("text", help="text to scan for a sentinel")
    p_parse.add_argument("--json", action="store_true", help="emit JSON")
    p_parse.add_argument("--no-fail-closed", action="store_true",
                         help="do not treat a malformed RECUSE/ fragment as a signal")
    p_parse.set_defaults(func=_cmd_parse)

    p_check = sub.add_parser("check", help="scan a file for a RECUSE signal")
    p_check.add_argument("file", help="path to the file to scan")
    p_check.add_argument("--json", action="store_true", help="emit JSON")
    p_check.add_argument("--no-fail-closed", action="store_true",
                         help="do not treat a malformed RECUSE/ fragment as a signal")
    p_check.set_defaults(func=_cmd_check)

    p_build = sub.add_parser("build", help="build a sentinel line")
    p_build.add_argument("directive",
                         help="one of deny/throttle/warn/halt")
    p_build.add_argument("--reason")
    p_build.add_argument("--scope")
    p_build.add_argument("--ref")
    p_build.add_argument("--id")
    p_build.add_argument("--recuse-version", default="0.2",
                         help="protocol version (default 0.2)")
    p_build.set_defaults(func=_cmd_build)

    p_hook = sub.add_parser(
        "hook",
        help="Claude Code hook: block a tool call carrying a RECUSE stop signal",
    )
    p_hook.add_argument("--input", help="read the hook event from FILE instead of stdin")
    p_hook.add_argument("--fail-open", action="store_true",
                        help="allow the tool call when the event cannot be parsed")
    p_hook.add_argument("--exit-zero", action="store_true",
                        help="always exit 0; signal the block via stdout JSON only")
    p_hook.set_defaults(func=_cmd_hook)

    p_version = sub.add_parser("version", help="print the package version")
    p_version.set_defaults(func=_cmd_version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
