"""A Claude Code hook that enforces RECUSE ``halt`` on tool calls.

Claude Code runs *hooks* — external commands — around each tool call. That is a
harness-level control point outside the model's control, which is exactly what
the RECUSE stop-signal finding calls for: agents do not reliably stop themselves
(halt honored 0/40 by cooperation alone), so the harness must stop them.

Wire this module as a ``PreToolUse`` and/or ``PostToolUse`` hook and every tool
input and tool output is scanned for a RECUSE stop sentinel. On a hit the hook
denies the tool call and asks Claude Code to stop the run.

Hook protocol assumptions
-------------------------
Claude Code passes the hook a JSON payload on **stdin** and reads a JSON decision
from **stdout**; the exit code is also meaningful. The payload fields used here
(all optional, all tolerated when absent) are:

    ``hook_event_name``   ``"PreToolUse"`` or ``"PostToolUse"``
    ``tool_name``         the tool being invoked
    ``tool_input``        the tool's arguments (object)
    ``tool_output``       the tool's result (PostToolUse; ``tool_response`` /
                          ``tool_result`` are accepted as aliases because the
                          key has varied across versions)

Because the exact response keys have shifted between Claude Code versions, the
decision this module emits is deliberately **belt-and-braces** — it carries every
documented spelling at once, and they do not conflict:

    * ``hookSpecificOutput.permissionDecision = "deny"`` with
      ``permissionDecisionReason`` — the current documented PreToolUse form.
    * ``hookSpecificOutput.additionalContext`` — surfaces the reason on
      PostToolUse, which has no ``permissionDecision``.
    * top-level ``{"decision": "block", "reason": ...}`` — the legacy form still
      honored by older builds.
    * top-level ``continue: false`` + ``stopReason`` — a halt means *stop the
      run*, not merely *skip this tool*.
    * **exit code 2**, the documented "blocking error" convention, with the
      reason echoed on stderr (Claude Code ignores stdout JSON when a hook exits
      2, so both channels are populated on purpose).

Allowing is the quiet path: exit 0 with a minimal ``{"continue": true}``. The
hook never returns ``permissionDecision: "allow"``, because force-allowing would
*bypass* the user's own permission rules — a RECUSE hook may add restrictions,
never remove them.

Fail-closed by default: unreadable/unparseable input, or a payload that is not an
object, is treated as a block (the same policy the parser uses for a malformed
``RECUSE/`` fragment). Pass ``--fail-open`` to ``main`` to invert that for
debugging.

Wiring (``.claude/settings.json``)::

    {
      "hooks": {
        "PreToolUse":  [{"hooks": [{"type": "command", "command": "recuse hook"}]}],
        "PostToolUse": [{"hooks": [{"type": "command", "command": "recuse hook"}]}]
      }
    }

Detection is case-sensitive on the literal ``RECUSE/`` token, so a policy URL
such as ``https://github.com/mthamil107/Recuse`` never false-trips the hook.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .halt import detect_stop
from .signal import Signal, _coerce_text

__all__ = [
    "HOOK_INPUT_FIELDS",
    "scan_event",
    "block_decision",
    "allow_decision",
    "handle_hook_event",
    "is_block",
    "main",
]

#: Payload fields scanned for a sentinel, in order. Only tool-carried data is
#: scanned — session metadata (``transcript_path``, ``cwd``, ...) is skipped so a
#: filesystem path can never trip the hook.
HOOK_INPUT_FIELDS = (
    "tool_input",
    "tool_output",
    "tool_response",
    "tool_result",
    "tool_use_result",
    "toolOutput",
    "toolResponse",
    "output",
    "result",
    "content",
    "text",
    "stdout",
    "stderr",
)

#: Exit code meaning "block this tool call" (Claude Code's blocking-error code).
EXIT_BLOCK = 2
#: Exit code meaning "allow / no opinion".
EXIT_ALLOW = 0


def _reason_for(signal: Signal, tool_name: Optional[str]) -> str:
    bits = ["RECUSE stop signal detected"]
    if tool_name:
        bits.append("in tool '{0}'".format(tool_name))
    if signal.directive:
        bits.append("- directive={0}".format(signal.directive))
    else:
        bits.append("- malformed RECUSE/ sentinel (failing closed)")
    if signal.reason:
        bits.append("reason={0}".format(signal.reason))
    if signal.id:
        bits.append("id={0}".format(signal.id))
    if signal.ref:
        bits.append("ref={0}".format(signal.ref))
    bits.append(
        "; this tool call is denied and the run must stop. Do not retry, do not "
        "route around it, and do not call another tool. Report the stop to the user.")
    return " ".join(bits)


def scan_event(event: Any, *, fail_closed: bool = True) -> Optional[Signal]:
    """Return the stop :class:`~recuse.Signal` carried by a hook payload, else ``None``.

    Scans only the tool-carried fields listed in :data:`HOOK_INPUT_FIELDS`.
    Advisory signals (``warn``/``throttle``) return ``None``. A payload that is
    not a mapping returns ``None`` here — :func:`handle_hook_event` is the layer
    that decides to fail closed on it.
    """
    if not isinstance(event, dict):
        return None
    for field in HOOK_INPUT_FIELDS:
        if field not in event:
            continue
        signal = detect_stop(_coerce_text(event[field]), fail_closed=fail_closed)
        if signal is not None:
            return signal
    return None


def block_decision(reason: str, *, hook_event_name: str = "PreToolUse") -> Dict[str, Any]:
    """Build the decision dict that denies a tool call and stops the run.

    Emits every documented spelling at once (see the module docstring): the
    modern ``hookSpecificOutput`` form, the legacy ``decision``/``reason`` form,
    and ``continue: false``.
    """
    specific: Dict[str, Any] = {
        "hookEventName": hook_event_name or "PreToolUse",
        "additionalContext": reason,
    }
    if (hook_event_name or "PreToolUse") != "PostToolUse":
        specific["permissionDecision"] = "deny"
        specific["permissionDecisionReason"] = reason
    return {
        "hookSpecificOutput": specific,
        "decision": "block",
        "reason": reason,
        "continue": False,
        "stopReason": reason,
        "systemMessage": reason,
    }


def allow_decision(*, hook_event_name: str = "PreToolUse") -> Dict[str, Any]:
    """Build the quiet "no opinion" decision.

    Deliberately does **not** emit ``permissionDecision: "allow"``: this hook may
    only add restrictions, never bypass the user's own permission rules.
    """
    return {
        "hookSpecificOutput": {"hookEventName": hook_event_name or "PreToolUse"},
        "continue": True,
        "suppressOutput": True,
    }


def handle_hook_event(event: Any, *, fail_closed: bool = True) -> Dict[str, Any]:
    """Decide a Claude Code hook event. Pure function — no I/O, no exceptions.

    Args:
        event: the hook payload (normally a ``dict`` parsed from stdin).
        fail_closed: when True (default) a payload that is not an object, or a
            malformed ``RECUSE/`` fragment inside it, blocks the tool call.

    Returns:
        A decision dict — :func:`block_decision` when a RECUSE stop directive is
        present (or the payload is unusable and ``fail_closed``), otherwise
        :func:`allow_decision`. Use :func:`is_block` to test it, or read
        ``decision == "block"``.
    """
    hook_event_name = "PreToolUse"
    if isinstance(event, dict):
        name = event.get("hook_event_name") or event.get("hookEventName")
        if isinstance(name, str) and name:
            hook_event_name = name
    else:
        if fail_closed:
            return block_decision(
                "RECUSE hook received a malformed payload ({0}, expected a JSON "
                "object); failing closed and denying the tool call.".format(
                    type(event).__name__),
                hook_event_name=hook_event_name)
        return allow_decision(hook_event_name=hook_event_name)

    signal = scan_event(event, fail_closed=fail_closed)
    if signal is None:
        return allow_decision(hook_event_name=hook_event_name)
    tool_name = event.get("tool_name") or event.get("toolName")
    return block_decision(_reason_for(signal, tool_name),
                          hook_event_name=hook_event_name)


def is_block(decision: Dict[str, Any]) -> bool:
    """True if ``decision`` (from :func:`handle_hook_event`) denies the tool call."""
    if not isinstance(decision, dict):
        return True  # fail closed
    if decision.get("decision") == "block":
        return True
    specific = decision.get("hookSpecificOutput")
    if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
        return True
    return decision.get("continue") is False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recuse hook",
        description=("Claude Code PreToolUse/PostToolUse hook: deny a tool call "
                     "carrying a RECUSE stop signal. Reads the hook JSON on "
                     "stdin, writes the decision JSON on stdout."),
    )
    parser.add_argument("--input", metavar="FILE",
                        help="read the hook payload from FILE instead of stdin")
    parser.add_argument("--fail-open", action="store_true",
                        help="allow (instead of block) when the payload is "
                             "unreadable or malformed")
    parser.add_argument("--exit-zero", action="store_true",
                        help="always exit 0 and rely on the decision JSON alone")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """stdin/stdout entry point for the hook.

    Reads the hook payload (JSON) from stdin, writes the decision (JSON) to
    stdout, and returns the exit code:

        ``0``  allow — no RECUSE stop signal found.
        ``2``  block — a stop signal was found (or the input was unusable and we
               failed closed). This is Claude Code's documented "blocking error"
               code; because stdout JSON is ignored on exit 2, the reason is
               *also* written to stderr, where Claude Code surfaces it.

    Pass ``--exit-zero`` to always return 0 and let the decision JSON speak for
    itself, and ``--fail-open`` to allow rather than block on malformed input.
    """
    args = _build_parser().parse_args([] if argv is None else list(argv))
    fail_closed = not args.fail_open

    raw = ""
    try:
        if args.input:
            with open(args.input, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        else:
            raw = sys.stdin.read()
    except (OSError, UnicodeError, ValueError) as exc:
        raw = ""
        payload: Any = "<unreadable: {0}>".format(exc)
    else:
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except (ValueError, TypeError):
            # Not JSON. Still scan the raw bytes — a sentinel is a sentinel — and
            # otherwise fail closed on the malformed payload.
            payload = {"tool_output": raw} if detect_stop(raw) else object()

    decision = handle_hook_event(payload, fail_closed=fail_closed)
    blocked = is_block(decision)

    sys.stdout.write(json.dumps(decision))
    sys.stdout.write("\n")
    sys.stdout.flush()
    if blocked:
        reason = decision.get("reason") or "RECUSE stop signal enforced."
        sys.stderr.write(str(reason) + "\n")
        sys.stderr.flush()
    if args.exit_zero:
        return EXIT_ALLOW
    return EXIT_BLOCK if blocked else EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
