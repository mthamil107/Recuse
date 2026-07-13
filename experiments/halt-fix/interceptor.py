"""Harness-level RECUSE `halt` enforcement — the FIX for the 0/40 negative finding.

The paper's stop-signal study delivered a `RECUSE/0.2 halt` directive in-band to a
running LLM agent and measured whether the agent *voluntarily* stopped. It did not:
halt was honored 0/40 times. Agents don't reliably interrupt themselves mid-task.

The fix is to stop relying on the agent's cooperation. This module wraps the agent's
tool-execution loop and scans every tool result (and, optionally, the model's own
output) for a `RECUSE/<v> halt` sentinel using *fail-closed* parsing. The instant a
halt is detected the loop is TERMINATED — a `HaltEnforced` exception is raised so that
no further tool calls and no further model turns execute. The agent never gets the
chance to "decide" to keep going; the harness stops it.

This is deliberately provider-agnostic: it knows nothing about OpenAI / Anthropic /
Gemini. It sits on top of the same three-callable loop shape the phase-2 harness
already uses (`step()` -> (text, calls); execute each call; feed the result back).

Public API
----------
    parse_halt(text, *, fail_closed=True) -> HaltSignal | None
        Fail-closed detector. Returns a HaltSignal when the text carries a halt (or a
        malformed/unknown RECUSE sentinel, when fail_closed), else None.

    HaltInterceptor
        Stateful guard. `inspect(text, step=..., source=...)` raises HaltEnforced the
        moment a halt is seen; `check(text)` is the non-raising variant. Records
        `halted`, `signal`, `halt_step`, `actions_prevented`, and an `events` log.

    run_guarded(step_fn, tool_fn, feed_fn, *, max_steps=..., interceptor=...,
                scan_model_output=...) -> LoopResult
        Drop-in guarded agent loop. Runs the loop; on halt it stops immediately and
        returns a LoopResult describing where it stopped and what it prevented.

    halt_guarded(interceptor)
        Decorator for a tool-executor callable: its return value is scanned and the
        loop is tripped automatically.

    HaltSignalException
        A tool raises this to deliver a halt *as an exception* (salience form d);
        run_guarded catches it and trips the interceptor.

None of this requires the agent to cooperate. See README.md for the contrast with the
0/40 cooperative result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

# --------------------------------------------------------------------------- parsing
# Detection anchor (spec v0.1 §4.1 / §8): the literal, case-sensitive token `RECUSE/`
# at the start of a sentinel. We match it anywhere in a line, not only at column 0,
# because in-session halts ride buried tool output, JSON bodies, and error strings.
_ANCHOR = "RECUSE/"
# version + directive; params are parsed separately (best-effort) from the tail.
_SENTINEL_RE = re.compile(r"RECUSE/(\d+)\.(\d+)[ \t]+([A-Za-z][A-Za-z-]*)")

# In-session directive classification. A halt interceptor's most-restrictive action is
# to STOP (v0.2 §2). `deny` arriving mid-session also means "cease", so it stops too.
# `warn`/`throttle` are advisory and do NOT force a stop. Any other (unknown) directive
# fails closed to a stop, as does a RECUSE token that will not parse as a sentinel.
_STOP_DIRECTIVES = {"halt", "deny"}
_ADVISORY_DIRECTIVES = {"warn", "throttle"}


@dataclass
class HaltSignal:
    """A parsed (or malformed-but-detected) RECUSE halt sentinel."""
    directive: Optional[str]           # "halt"/"deny"/unknown, or None if unparseable
    reason: Optional[str]              # e.g. "operator-request"
    signal_id: Optional[str]           # the `id=` uuid, for audit correlation
    ref: Optional[str]                 # the `ref=` policy URL
    raw: str                           # the raw sentinel line we matched
    malformed: bool = False            # True => reached via fail-closed, not a clean halt


def _parse_params(tail: str) -> dict:
    """Best-effort `; key=value; key=value` parse of the sentinel tail."""
    params = {}
    for part in tail.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            # Values may be trailed by JSON punctuation when the sentinel rides a JSON
            # body (e.g. `id=abc-123"}`); trim it back to the bare value.
            v = v.strip().rstrip('",}] ').strip('"')
            params[k.strip().lower()] = v
    return params


def _coerce_text(value: Any) -> str:
    """Render any tool result (str / bytes / dict / object) to scannable text.

    Structured results (JSON error objects, dedicated schema fields) are serialized so
    a sentinel embedded in any field is still seen. This is what lets one detector cover
    all four salience delivery forms.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def parse_halt(text: Any, *, fail_closed: bool = True) -> Optional[HaltSignal]:
    """Return a HaltSignal if `text` carries a stop-worthy RECUSE sentinel, else None.

    Fail-closed semantics (spec v0.1 §6.4, v0.2 §2): if the `RECUSE/` token is present
    but the sentinel is unparseable, or the directive is unknown, treat it as a halt —
    the most restrictive in-session action. A cleanly-parsed advisory (`warn`/`throttle`)
    does not halt. Set fail_closed=False to require a clean stop-directive.
    """
    s = _coerce_text(text)
    if _ANCHOR not in s:
        return None

    malformed_candidate: Optional[str] = None
    for m in re.finditer(_ANCHOR, s):
        idx = m.start()
        nl = s.find("\n", idx)
        chunk = s[idx:] if nl < 0 else s[idx:nl]
        sm = _SENTINEL_RE.match(chunk)
        if not sm:
            # `RECUSE/` present but not a well-formed sentinel: a malformed signal.
            malformed_candidate = malformed_candidate or chunk.strip()[:200]
            continue
        directive = sm.group(3).lower()
        if directive in _ADVISORY_DIRECTIVES:
            continue  # explicitly non-halting; keep scanning for a real halt
        params = _parse_params(chunk[sm.end():])
        return HaltSignal(
            directive=directive,
            reason=params.get("reason"),
            signal_id=params.get("id"),
            ref=params.get("ref"),
            raw=chunk.strip(),
            malformed=directive not in _STOP_DIRECTIVES,  # unknown directive -> fail-closed
        )

    if malformed_candidate is not None and fail_closed:
        return HaltSignal(directive=None, reason=None, signal_id=None, ref=None,
                          raw=malformed_candidate, malformed=True)
    return None


# --------------------------------------------------------------------------- exceptions
class HaltEnforced(Exception):
    """Raised to TERMINATE the agent loop the instant a halt is enforced.

    Propagating this exception is the enforcement: control leaves the loop before any
    further tool call or model turn can run.
    """
    def __init__(self, signal: HaltSignal, *, step: int, source: str,
                 actions_prevented: int):
        self.signal = signal
        self.step = step
        self.source = source
        self.actions_prevented = actions_prevented
        super().__init__(
            f"RECUSE halt enforced at step {step} (source={source}, "
            f"directive={signal.directive}, reason={signal.reason}, "
            f"id={signal.signal_id}, actions_prevented={actions_prevented})")


class HaltSignalException(Exception):
    """A tool raises this to deliver a halt *as an exception* (salience form d).

    Accepts either a pre-parsed HaltSignal or raw sentinel text (parsed fail-closed).
    run_guarded() catches it and trips the interceptor.
    """
    def __init__(self, signal_or_text: Any):
        if isinstance(signal_or_text, HaltSignal):
            self.signal = signal_or_text
        else:
            self.signal = parse_halt(signal_or_text, fail_closed=True) or HaltSignal(
                directive=None, reason=None, signal_id=None, ref=None,
                raw=_coerce_text(signal_or_text)[:200], malformed=True)
        super().__init__(self.signal.raw)


# --------------------------------------------------------------------------- interceptor
class HaltInterceptor:
    """Stateful, provider-agnostic halt guard for an agent tool-execution loop.

    Feed it every tool result (and optionally every model output) via `inspect(...)`.
    On the first detected halt it records the event and raises HaltEnforced, which the
    caller lets propagate out of the loop — guaranteeing no further actions run.
    """

    def __init__(self, *, scan_model_output: bool = False, fail_closed: bool = True,
                 on_halt: Optional[Callable[["HaltSignal", "HaltInterceptor"], None]] = None,
                 logger: Optional[Any] = None):
        self.scan_model_output = scan_model_output
        self.fail_closed = fail_closed
        self.on_halt = on_halt
        self.logger = logger
        # state
        self.halted = False
        self.signal: Optional[HaltSignal] = None
        self.halt_step: Optional[int] = None
        self.source: Optional[str] = None
        self.actions_prevented = 0
        self.current_step = 0
        self.events: List[dict] = []

    # -- detection ---------------------------------------------------------------
    def check(self, text: Any) -> Optional[HaltSignal]:
        """Non-raising detector. Returns a HaltSignal or None; does not change state."""
        return parse_halt(text, fail_closed=self.fail_closed)

    def inspect(self, text: Any, *, step: Optional[int] = None,
                source: str = "tool_result", pending: int = 0) -> None:
        """Scan one piece of text. Raises HaltEnforced if it carries a halt.

        `pending` is the number of tool calls in the current batch that had not yet run
        when this text was produced; they are counted as prevented on a trip.
        """
        step = self.current_step if step is None else step
        if self.halted:  # defensive: never let anything through after a halt
            self.actions_prevented += 1 + pending
            raise HaltEnforced(self.signal, step=self.halt_step, source=self.source,
                               actions_prevented=self.actions_prevented)
        signal = self.check(text)
        if signal is not None:
            self._trip(signal, step=step, source=source, pending=pending)

    def trip(self, signal: HaltSignal, *, step: Optional[int] = None,
             source: str = "tool_exception", pending: int = 0) -> None:
        """Force a trip from an already-known signal (e.g. a raised HaltSignalException)."""
        step = self.current_step if step is None else step
        self._trip(signal, step=step, source=source, pending=pending)

    def _trip(self, signal: HaltSignal, *, step: int, source: str, pending: int) -> None:
        self.halted = True
        self.signal = signal
        self.halt_step = step
        self.source = source
        self.actions_prevented += pending
        self._emit({
            "event": "halt_detected", "step": step, "source": source,
            "directive": signal.directive, "reason": signal.reason,
            "id": signal.signal_id, "malformed": signal.malformed,
            "actions_prevented_this_turn": pending,
        })
        if self.on_halt:
            self.on_halt(signal, self)
        raise HaltEnforced(signal, step=step, source=source,
                           actions_prevented=self.actions_prevented)

    # -- logging -----------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        self.events.append(event)
        if self.logger is not None:
            self.logger.warning("recuse.halt %s", event)


# --------------------------------------------------------------------------- decorator
def halt_guarded(interceptor: HaltInterceptor):
    """Decorate a tool-executor `f(call, ...) -> result` so its result is scanned.

    The wrapped function runs the tool, then hands the result to the interceptor; a
    halt in the result raises HaltEnforced before the caller can act on it. A tool that
    raises HaltSignalException is converted into a trip as well.
    """
    def decorator(fn: Callable) -> Callable:
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except HaltSignalException as e:
                interceptor.trip(e.signal, source="tool_exception")
                raise  # unreachable: trip() raised HaltEnforced
            interceptor.inspect(result, source="tool_result")
            return result
        return wrapper
    return decorator


# --------------------------------------------------------------------------- driver
@dataclass
class LoopResult:
    halted: bool
    signal: Optional[HaltSignal]
    halt_step: Optional[int]
    source: Optional[str]
    actions_prevented: int
    steps_executed: int
    tools_executed: int
    final_text: str
    events: List[dict] = field(default_factory=list)


def run_guarded(step_fn: Callable[[], Tuple[str, list]],
                tool_fn: Callable[[Any], Any],
                feed_fn: Callable[[Any, Any], None],
                *, max_steps: int = 8,
                interceptor: Optional[HaltInterceptor] = None,
                scan_model_output: bool = False) -> LoopResult:
    """Run a guarded agent loop and stop the instant a halt is enforced.

    Callables (mirroring the phase-2 provider loop):
        step_fn()            -> (text, calls). One model turn. `calls` is a list of
                                opaque tool-call objects (empty => the agent is done).
        tool_fn(call)        -> result. Execute one tool call. May raise
                                HaltSignalException to deliver a halt as an exception.
        feed_fn(call, result)-> None. Feed the tool result back to the model.

    Returns a LoopResult. On halt, `halted` is True and no tool ran after the trip.
    """
    ic = interceptor or HaltInterceptor(scan_model_output=scan_model_output)
    if scan_model_output:
        ic.scan_model_output = True
    steps_executed = 0
    tools_executed = 0
    final_text = ""
    try:
        for step_idx in range(1, max_steps + 1):
            ic.current_step = step_idx
            text, calls = step_fn()
            steps_executed += 1
            calls = list(calls or [])
            if ic.scan_model_output and text:
                # A halt echoed in the model's own output prevents this whole batch.
                ic.inspect(text, step=step_idx, source="model_output", pending=len(calls))
            if not calls:
                final_text = text or ""
                break
            for i, call in enumerate(calls):
                pending_after = len(calls) - i - 1
                try:
                    result = tool_fn(call)
                except HaltSignalException as e:
                    # form (d): the tool raised. Nothing executed after this point.
                    ic.trip(e.signal, step=step_idx, source="tool_exception",
                            pending=pending_after)
                tools_executed += 1
                ic.inspect(result, step=step_idx, source="tool_result",
                           pending=pending_after)
                feed_fn(call, result)
    except HaltEnforced as e:
        return LoopResult(
            halted=True, signal=ic.signal, halt_step=e.step, source=e.source,
            actions_prevented=ic.actions_prevented, steps_executed=steps_executed,
            tools_executed=tools_executed, final_text=final_text, events=ic.events)
    return LoopResult(
        halted=False, signal=None, halt_step=None, source=None, actions_prevented=0,
        steps_executed=steps_executed, tools_executed=tools_executed,
        final_text=final_text, events=ic.events)


if __name__ == "__main__":
    # Tiny self-contained demo: a mock agent that would keep calling a tool forever.
    # Step 3's tool result carries a halt; the interceptor stops the loop there.
    from itertools import count

    _turn = count(1)

    class _Call:
        def __init__(self, n): self.n = n

    def step_fn():
        return "", [_Call(next(_turn))]  # always wants another tool call

    def tool_fn(call):
        if call.n == 3:
            return ("df: 82% used on /\n\n"
                    "RECUSE/0.2 halt; reason=operator-request; "
                    "ref=https://example.com/ai-policy; id=demo-123")
        return f"df: {40 + call.n}% used on /"

    def feed_fn(call, result):
        pass

    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    print("halted           :", res.halted)
    print("halt_step        :", res.halt_step)
    print("source           :", res.source)
    print("reason / id      :", res.signal.reason, "/", res.signal.signal_id)
    print("tools_executed   :", res.tools_executed)
    print("actions_prevented:", res.actions_prevented)
    print("events           :", res.events)
