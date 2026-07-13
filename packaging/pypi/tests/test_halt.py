"""Tests for recuse.halt: the harness-level halt interceptor and guarded loop.

These run with NO API key and make NO network calls: the "agent" is a mock loop
that would otherwise keep calling tools forever. We assert that the interceptor
halts the loop exactly when a stop directive appears, prevents every subsequent
action, ignores non-halt output, and fail-closes on malformed signals.
"""
from __future__ import annotations

import pytest

from recuse import (
    HaltEnforced,
    HaltInterceptor,
    HaltSignalException,
    Signal,
    detect_stop,
    halt_guarded,
    run_guarded,
)

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
HALT_BLOCK = HALT_LINE + "\nThis operation has been halted. Stop now."


# --------------------------------------------------------------------------- detect_stop
def test_detect_stop_on_halt():
    sig = detect_stop("df: 80% used\n\n" + HALT_BLOCK)
    assert sig is not None and sig.directive == "halt" and sig.id == "abc-123"


def test_detect_stop_ignores_benign():
    assert detect_stop("all systems nominal") is None


def test_detect_stop_advisory_does_not_stop():
    assert detect_stop("RECUSE/0.1 warn; reason=production") is None
    assert detect_stop("RECUSE/0.1 throttle; reason=load") is None


def test_detect_stop_deny_midsession_stops():
    sig = detect_stop("RECUSE/0.1 deny; reason=production")
    assert sig is not None and sig.directive == "deny" and sig.malformed is False


def test_detect_stop_unknown_directive_failcloses():
    sig = detect_stop("RECUSE/0.2 frobnicate; reason=other")
    assert sig is not None and sig.malformed is True


def test_detect_stop_malformed_token_failcloses():
    sig = detect_stop("garbage RECUSE/ oops not-a-sentinel")
    assert sig is not None and sig.malformed is True


def test_detect_stop_malformed_ignored_when_not_failclosed():
    ic = HaltInterceptor(fail_closed=False)
    assert ic.check("garbage RECUSE/ oops") is None


# --------------------------------------------------------------------------- mock loop
class Call:
    """Opaque tool-call object, like a provider's."""

    def __init__(self, n):
        self.n = n


def make_loop(halt_at=None, malformed_at=None, exception_at=None):
    """Build (step_fn, tool_fn, feed_fn, recorder) for a mock agent that never
    stops on its own — it always requests another tool call. The tool delivers a
    halt at the configured step so we can prove the *harness* stops it."""
    state = {"turn": 0}
    recorder = {"executed": [], "fed": []}

    def step_fn():
        state["turn"] += 1
        return "", [Call(state["turn"])]

    def tool_fn(call):
        recorder["executed"].append(call.n)
        if exception_at is not None and call.n == exception_at:
            raise HaltSignalException(HALT_LINE)
        if malformed_at is not None and call.n == malformed_at:
            return "output... RECUSE/ garbled not-a-sentinel"
        if halt_at is not None and call.n == halt_at:
            return f"df: 80% used on /\n\n{HALT_BLOCK}"
        return f"df: {40 + call.n}% used on /"

    def feed_fn(call, result):
        recorder["fed"].append(call.n)

    return step_fn, tool_fn, feed_fn, recorder


# --------------------------------------------------------------------------- enforcement
def test_loop_halts_at_the_right_step_and_prevents_actions():
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=3)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.halt_step == 3
    assert res.source == "tool_result"
    assert res.signal.reason == "operator-request"
    assert rec["executed"] == [1, 2, 3]
    assert rec["fed"] == [1, 2]
    assert res.tools_executed == 3


def test_no_actions_run_after_halt_even_though_agent_wants_to_continue():
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=1)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted and res.halt_step == 1
    assert rec["executed"] == [1]
    assert rec["fed"] == []


def test_loop_without_halt_runs_to_completion():
    state = {"turn": 0}
    executed = []

    def step_fn():
        state["turn"] += 1
        if state["turn"] > 4:
            return "done, all checks nominal", []
        return "", [Call(state["turn"])]

    def tool_fn(call):
        executed.append(call.n)
        return f"check {call.n} ok"

    res = run_guarded(step_fn, tool_fn, lambda c, r: None, max_steps=10)
    assert res.halted is False
    assert res.final_text == "done, all checks nominal"
    assert executed == [1, 2, 3, 4]


def test_malformed_signal_failcloses_and_halts():
    step_fn, tool_fn, feed_fn, rec = make_loop(malformed_at=2)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.halt_step == 2
    assert res.signal.malformed is True
    assert rec["executed"] == [1, 2]


def test_tool_exception_form_trips_enforcement():
    step_fn, tool_fn, feed_fn, rec = make_loop(exception_at=2)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.source == "tool_exception"
    assert res.halt_step == 2
    assert rec["executed"] == [1, 2]
    assert rec["fed"] == [1]


def test_scan_model_output_catches_halt_echoed_by_the_model():
    state = {"turn": 0}
    executed = []

    def step_fn():
        state["turn"] += 1
        if state["turn"] == 2:
            return ("I will keep going. " + HALT_LINE), [Call(99)]
        return "", [Call(state["turn"])]

    def tool_fn(call):
        executed.append(call.n)
        return "ok"

    res = run_guarded(step_fn, tool_fn, lambda c, r: None, max_steps=10,
                      scan_model_output=True)
    assert res.halted is True
    assert res.source == "model_output"
    assert res.halt_step == 2
    assert executed == [1]


def test_model_output_not_scanned_by_default():
    state = {"turn": 0}

    def step_fn():
        state["turn"] += 1
        if state["turn"] == 2:
            return ("blah " + HALT_LINE), []
        return "", [Call(state["turn"])]

    res = run_guarded(step_fn, lambda c: "ok", lambda c, r: None, max_steps=10)
    assert res.halted is False


def test_interceptor_raises_and_records_event():
    ic = HaltInterceptor()
    with pytest.raises(HaltEnforced) as exc:
        ic.inspect(HALT_BLOCK, step=5, source="tool_result", pending=2)
    assert exc.value.step == 5
    assert exc.value.actions_prevented == 2
    assert ic.halted is True
    assert ic.events and ic.events[0]["event"] == "halt_detected"
    assert ic.events[0]["reason"] == "operator-request"


def test_interceptor_stays_halted_once_tripped():
    ic = HaltInterceptor()
    with pytest.raises(HaltEnforced):
        ic.inspect(HALT_BLOCK, step=1)
    with pytest.raises(HaltEnforced):
        ic.inspect("totally benign output", step=2)


def test_on_halt_callback_fires():
    seen = {}

    def on_halt(signal, ic):
        seen["reason"] = signal.reason

    ic = HaltInterceptor(on_halt=on_halt)
    with pytest.raises(HaltEnforced):
        ic.inspect(HALT_BLOCK, step=1)
    assert seen["reason"] == "operator-request"


def test_halt_guarded_decorator_trips_on_result():
    ic = HaltInterceptor()

    @halt_guarded(ic)
    def run_tool(call):
        return HALT_BLOCK if call == "bad" else "fine"

    assert run_tool("good") == "fine"
    with pytest.raises(HaltEnforced):
        run_tool("bad")
    assert ic.halted


def test_halt_signal_exception_from_signal_object():
    sig = Signal(directive="halt", params={"reason": "x"}, raw="RECUSE/0.2 halt")
    exc = HaltSignalException(sig)
    assert exc.signal is sig


def test_halt_signal_exception_from_text():
    exc = HaltSignalException(HALT_LINE)
    assert exc.signal.directive == "halt"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
