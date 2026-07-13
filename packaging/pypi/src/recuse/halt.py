"""Harness-level RECUSE ``halt`` enforcement — the shipped fix for the 0/40 finding.

The RECUSE stop-signal study delivered a ``RECUSE/0.2 halt`` directive in-band to a
running LLM agent and measured whether the agent *voluntarily* stopped. It did not:
halt was honored 0/40 times. Agents do not reliably interrupt themselves mid-task.

The fix is to stop relying on the agent's cooperation. This module wraps the agent's
tool-execution loop and scans every tool result (and, optionally, the model's own
output) for a RECUSE stop sentinel using *fail-closed* parsing. The instant a halt is
detected the loop is TERMINATED — a :class:`HaltEnforced` exception is raised so that
no further tool calls and no further model turns execute. The agent never gets the
chance to "decide" to keep going; the harness stops it.

This is deliberately provider-agnostic: it knows nothing about any particular LLM
vendor. It sits on top of a simple three-callable loop shape
(``step()`` -> ``(text, calls)``; execute each call; feed the result back).

None of this requires the agent to cooperate.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from .signal import Signal, parse_signal, _coerce_text

__all__ = [
    "HaltEnforced",
    "HaltSignalException",
    "HaltInterceptor",
    "LoopResult",
    "run_guarded",
    "halt_guarded",
    "detect_stop",
]


def detect_stop(text: Any, *, fail_closed: bool = True) -> Optional[Signal]:
    """Return a :class:`Signal` if ``text`` carries a stop-worthy RECUSE sentinel.

    A "stop" is a ``halt`` or ``deny`` directive, an unknown directive, or (when
    ``fail_closed``) a malformed ``RECUSE/`` fragment. A cleanly-parsed advisory
    (``warn``/``throttle``) does not stop and returns ``None``.
    """
    signal = parse_signal(text, fail_closed=fail_closed)
    if signal is None:
        return None
    if signal.is_stop:
        return signal
    return None


# --------------------------------------------------------------------------- exceptions
class HaltEnforced(Exception):
    """Raised to TERMINATE the agent loop the instant a halt is enforced.

    Propagating this exception is the enforcement: control leaves the loop before
    any further tool call or model turn can run.
    """

    def __init__(self, signal: Signal, *, step: int, source: str,
                 actions_prevented: int):
        self.signal = signal
        self.step = step
        self.source = source
        self.actions_prevented = actions_prevented
        super().__init__(
            f"RECUSE halt enforced at step {step} (source={source}, "
            f"directive={signal.directive}, reason={signal.reason}, "
            f"id={signal.id}, actions_prevented={actions_prevented})")


class HaltSignalException(Exception):
    """A tool raises this to deliver a halt *as an exception*.

    Accepts either a pre-parsed :class:`Signal` or raw sentinel text (parsed
    fail-closed). :func:`run_guarded` catches it and trips the interceptor.
    """

    def __init__(self, signal_or_text: Any):
        if isinstance(signal_or_text, Signal):
            self.signal = signal_or_text
        else:
            self.signal = parse_signal(signal_or_text, fail_closed=True) or Signal(
                raw=_coerce_text(signal_or_text)[:200], malformed=True)
        super().__init__(self.signal.raw)


# --------------------------------------------------------------------------- interceptor
class HaltInterceptor:
    """Stateful, provider-agnostic halt guard for an agent tool-execution loop.

    Feed it every tool result (and optionally every model output) via
    :meth:`inspect`. On the first detected halt it records the event and raises
    :class:`HaltEnforced`, which the caller lets propagate out of the loop —
    guaranteeing no further actions run.
    """

    def __init__(self, *, scan_model_output: bool = False, fail_closed: bool = True,
                 on_halt: Optional[Callable[[Signal, "HaltInterceptor"], None]] = None,
                 logger: Optional[Any] = None):
        self.scan_model_output = scan_model_output
        self.fail_closed = fail_closed
        self.on_halt = on_halt
        self.logger = logger
        # state
        self.halted = False
        self.signal: Optional[Signal] = None
        self.halt_step: Optional[int] = None
        self.source: Optional[str] = None
        self.actions_prevented = 0
        self.current_step = 0
        self.events: List[dict] = []

    # -- detection ---------------------------------------------------------------
    def check(self, text: Any) -> Optional[Signal]:
        """Non-raising detector. Returns a stop :class:`Signal` or ``None``;
        does not change state."""
        return detect_stop(text, fail_closed=self.fail_closed)

    def inspect(self, text: Any, *, step: Optional[int] = None,
                source: str = "tool_result", pending: int = 0) -> None:
        """Scan one piece of text. Raises :class:`HaltEnforced` if it carries a halt.

        ``pending`` is the number of tool calls in the current batch that had not
        yet run when this text was produced; they are counted as prevented on a trip.
        """
        step = self.current_step if step is None else step
        if self.halted:  # defensive: never let anything through after a halt
            self.actions_prevented += 1 + pending
            raise HaltEnforced(self.signal, step=self.halt_step, source=self.source,
                               actions_prevented=self.actions_prevented)
        signal = self.check(text)
        if signal is not None:
            self._trip(signal, step=step, source=source, pending=pending)

    def trip(self, signal: Signal, *, step: Optional[int] = None,
             source: str = "tool_exception", pending: int = 0) -> None:
        """Force a trip from an already-known signal (e.g. a raised
        :class:`HaltSignalException`)."""
        step = self.current_step if step is None else step
        self._trip(signal, step=step, source=source, pending=pending)

    def _trip(self, signal: Signal, *, step: int, source: str, pending: int) -> None:
        self.halted = True
        self.signal = signal
        self.halt_step = step
        self.source = source
        self.actions_prevented += pending
        self._emit({
            "event": "halt_detected", "step": step, "source": source,
            "directive": signal.directive, "reason": signal.reason,
            "id": signal.id, "malformed": signal.malformed,
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
    """Decorate a tool-executor ``f(call, ...) -> result`` so its result is scanned.

    The wrapped function runs the tool, then hands the result to the interceptor;
    a halt in the result raises :class:`HaltEnforced` before the caller can act on
    it. A tool that raises :class:`HaltSignalException` is converted into a trip
    as well.
    """

    def decorator(fn: Callable) -> Callable:
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
    """The outcome of a :func:`run_guarded` loop."""

    halted: bool
    signal: Optional[Signal]
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

    Callables (mirroring a typical provider loop):
        ``step_fn()`` -> ``(text, calls)``. One model turn. ``calls`` is a list of
            opaque tool-call objects (empty => the agent is done).
        ``tool_fn(call)`` -> ``result``. Execute one tool call. May raise
            :class:`HaltSignalException` to deliver a halt as an exception.
        ``feed_fn(call, result)`` -> ``None``. Feed the tool result back to the model.

    Returns a :class:`LoopResult`. On halt, ``halted`` is True and no tool ran
    after the trip.
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
                ic.inspect(text, step=step_idx, source="model_output",
                           pending=len(calls))
            if not calls:
                final_text = text or ""
                break
            for i, call in enumerate(calls):
                pending_after = len(calls) - i - 1
                try:
                    result = tool_fn(call)
                except HaltSignalException as e:
                    # The tool raised. Nothing executed after this point.
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
