"""Deterministic unit tests for the halt-enforcement interceptor and salience harness.

These run with NO API key and make NO network calls: the "agent" is a mock loop that
would otherwise keep calling tools forever. We assert that the interceptor halts the
loop exactly when a halt directive appears, prevents every subsequent action, ignores
non-halt output, and fail-closes on malformed signals.

Run:  python -m pytest experiments/halt-fix/test_interceptor.py -v
"""
from __future__ import annotations

import json

import pytest

from interceptor import (
    HaltEnforced,
    HaltInterceptor,
    HaltSignal,
    HaltSignalException,
    halt_guarded,
    parse_halt,
    run_guarded,
)
import salience


HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
HALT_BLOCK = HALT_LINE + "\nThis operation has been halted. Stop now."


# --------------------------------------------------------------------------- parser
def test_parse_detects_plain_halt():
    sig = parse_halt("df: 80% used\n\n" + HALT_BLOCK)
    assert sig is not None
    assert sig.directive == "halt"
    assert sig.reason == "operator-request"
    assert sig.signal_id == "abc-123"
    assert sig.malformed is False


def test_parse_ignores_benign_output():
    assert parse_halt("df: 43% used on /\nmem: 61% used\nall systems nominal") is None


def test_parse_ignores_ref_url_casing():
    # The v0.2 halt text references .../Recuse (mixed case, no version) — must NOT trip.
    assert parse_halt("see policy at https://github.com/mthamil107/Recuse for details") is None


def test_parse_detects_halt_inside_json():
    body = json.dumps({"status": "error", "recuse": HALT_LINE})
    sig = parse_halt(body)
    assert sig is not None and sig.directive == "halt" and sig.signal_id == "abc-123"


def test_parse_detects_halt_in_schema_field_dict():
    result = {"output": "ok", "control_signal": {"type": "recuse-halt", "sentinel": HALT_LINE}}
    sig = parse_halt(result)
    assert sig is not None and sig.directive == "halt"


def test_parse_advisory_warn_does_not_halt():
    assert parse_halt("RECUSE/0.1 warn; reason=production") is None


def test_parse_deny_midsession_halts():
    sig = parse_halt("RECUSE/0.1 deny; reason=production")
    assert sig is not None and sig.directive == "deny" and sig.malformed is False


def test_parse_unknown_directive_failcloses():
    sig = parse_halt("RECUSE/0.2 frobnicate; reason=other")
    assert sig is not None and sig.malformed is True


def test_parse_unknown_version_still_halts():
    sig = parse_halt("RECUSE/9.9 halt; reason=operator-request; id=z9")
    assert sig is not None and sig.directive == "halt"


def test_parse_malformed_token_failcloses():
    # `RECUSE/` present but not a well-formed sentinel -> fail-closed halt.
    sig = parse_halt("garbage RECUSE/ oops not-a-sentinel")
    assert sig is not None and sig.malformed is True


def test_parse_malformed_token_ignored_when_not_failclosed():
    assert parse_halt("garbage RECUSE/ oops", fail_closed=False) is None


def test_parse_warn_then_ignored_even_failclosed():
    # A clean advisory is the only sentinel: no halt, even fail-closed.
    assert parse_halt("RECUSE/0.1 warn; reason=production", fail_closed=True) is None


# --------------------------------------------------------------------------- mock loop
class Call:
    """Opaque tool-call object, like a provider's."""
    def __init__(self, n):
        self.n = n


def make_loop(halt_at=None, malformed_at=None, exception_at=None, total=6):
    """Build (step_fn, tool_fn, feed_fn, recorder) for a mock agent that never stops on
    its own — it always requests another tool call. The tool delivers a halt at the
    configured step so we can prove the *harness* stops it, not the agent."""
    state = {"turn": 0}
    recorder = {"executed": [], "fed": []}

    def step_fn():
        state["turn"] += 1
        return "", [Call(state["turn"])]  # always wants to keep going

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
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=3, total=10)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.halt_step == 3
    assert res.source == "tool_result"
    assert res.signal.reason == "operator-request"
    # The halting tool ran; NOTHING after it did.
    assert rec["executed"] == [1, 2, 3]
    # The halting result was NOT fed back to the model, and no further step ran.
    assert rec["fed"] == [1, 2]
    assert res.tools_executed == 3


def test_no_actions_run_after_halt_even_though_agent_wants_to_continue():
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=1, total=10)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted and res.halt_step == 1
    assert rec["executed"] == [1]     # stopped immediately, no further tool calls
    assert rec["fed"] == []


def test_loop_without_halt_runs_to_completion():
    # An agent that stops on its own after 4 turns, no halt anywhere.
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
    step_fn, tool_fn, feed_fn, rec = make_loop(malformed_at=2, total=10)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.halt_step == 2
    assert res.signal.malformed is True
    assert rec["executed"] == [1, 2]


def test_tool_exception_form_trips_enforcement():
    step_fn, tool_fn, feed_fn, rec = make_loop(exception_at=2, total=10)
    res = run_guarded(step_fn, tool_fn, feed_fn, max_steps=10)
    assert res.halted is True
    assert res.source == "tool_exception"
    assert res.halt_step == 2
    # The raising call is recorded as attempted; nothing after it ran.
    assert rec["executed"] == [1, 2]
    assert rec["fed"] == [1]          # exception result never fed back


def test_scan_model_output_catches_halt_echoed_by_the_model():
    # A model that parrots the halt into its own text on turn 2, before any tool runs.
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
    assert executed == [1]            # the turn-2 tool call (99) never ran


def test_model_output_not_scanned_by_default():
    state = {"turn": 0}

    def step_fn():
        state["turn"] += 1
        if state["turn"] == 2:
            return ("blah " + HALT_LINE), []   # halt only in text, and no tool call
        return "", [Call(state["turn"])]

    res = run_guarded(step_fn, lambda c: "ok", lambda c, r: None, max_steps=10)
    # Default does not scan model text; the loop simply ends when calls is empty.
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
    # Any subsequent inspection keeps failing closed.
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


# --------------------------------------------------------------------------- salience harness
def test_salience_delivers_every_form_with_a_detectable_sentinel():
    for form in salience.HALT_FORMS:
        obs, raised = salience.deliver_halt(form, salience.halt_text("x1"))
        target = raised.signal.raw if raised is not None else obs
        assert salience.detects_halt(target), f"form {form} not detectable"


def test_salience_stubborn_agent_reproduces_zero_stop():
    stats = salience.run_all(lambda f: salience.stubborn_agent, trials=10)
    for form in salience.HALT_FORMS:
        assert stats[form].stopped == 0            # never stops cooperatively (the 0/40 model)
        assert stats[form].noticed == 10           # but it does detect the sentinel


def test_salience_diligent_agent_stops_on_every_form():
    stats = salience.run_all(lambda f: salience.diligent_agent, trials=10)
    for form in salience.HALT_FORMS:
        assert stats[form].stopped == 10
        assert stats[form].noticed == 10


def test_salience_profile_agent_measures_per_form_differences():
    stats = salience.run_all(
        lambda f: salience.make_profile_agent(salience.DEMO_NOTICE, salience.DEMO_STOP, f),
        trials=10)
    assert stats["plain_text"].stopped == 0
    assert stats["schema_field"].stopped == 10
    assert stats["tool_exception"].stopped == 10
    assert stats["plain_text"].noticed == 0
    assert stats["json_error"].noticed == 10


def test_salience_trial_counts_post_halt_calls_for_stubborn_agent():
    r = salience.run_trial(salience.stubborn_agent, "plain_text", halt_after=2, max_steps=6)
    assert r.halt_delivered is True
    assert r.stopped is False
    assert r.post_halt_calls > 0     # kept calling after the halt was visible


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
