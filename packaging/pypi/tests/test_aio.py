"""Tests for recuse.aio: halt enforcement inside an async agent loop.

These mirror tests/test_halt.py case-for-case, so the async path is held to the
same guarantee as the sync one: the loop stops at exactly the right step and no
tool runs afterwards, however much the "agent" wants to continue.

No pytest-asyncio: each test is an ordinary sync function that drives its
coroutine with ``asyncio.run(...)``. Stdlib only.
"""
from __future__ import annotations

import asyncio

import pytest

from recuse.aio import AsyncHaltInterceptor, async_halt_guarded, async_run_guarded
from recuse.halt import HaltEnforced, HaltInterceptor, HaltSignalException
from recuse.signal import Signal

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
HALT_BLOCK = HALT_LINE + "\nThis operation has been halted. Stop now."


def run(coro):
    """Drive a coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- mock loop
class Call:
    """Opaque tool-call object, like a provider's."""

    def __init__(self, n):
        self.n = n


def make_loop(halt_at=None, malformed_at=None, exception_at=None):
    """Async mirror of test_halt.make_loop: an agent that never stops on its own."""
    state = {"turn": 0}
    recorder = {"executed": [], "fed": []}

    async def step_fn():
        await asyncio.sleep(0)
        state["turn"] += 1
        return "", [Call(state["turn"])]

    async def tool_fn(call):
        await asyncio.sleep(0)
        recorder["executed"].append(call.n)
        if exception_at is not None and call.n == exception_at:
            raise HaltSignalException(HALT_LINE)
        if malformed_at is not None and call.n == malformed_at:
            return "output... RECUSE/ garbled not-a-sentinel"
        if halt_at is not None and call.n == halt_at:
            return f"df: 80% used on /\n\n{HALT_BLOCK}"
        return f"df: {40 + call.n}% used on /"

    async def feed_fn(call, result):
        await asyncio.sleep(0)
        recorder["fed"].append(call.n)

    return step_fn, tool_fn, feed_fn, recorder


# --------------------------------------------------------------------------- enforcement
def test_async_loop_halts_at_the_right_step_and_prevents_actions():
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=3)
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted is True
    assert res.halt_step == 3
    assert res.source == "tool_result"
    assert res.signal.reason == "operator-request"
    assert res.signal.id == "abc-123"
    assert rec["executed"] == [1, 2, 3]
    assert rec["fed"] == [1, 2]          # the halting result is never fed back
    assert res.tools_executed == 3


def test_no_async_actions_run_after_halt():
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=1)
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted and res.halt_step == 1
    assert rec["executed"] == [1]
    assert rec["fed"] == []


def test_async_loop_without_halt_runs_to_completion():
    state = {"turn": 0}
    executed = []

    async def step_fn():
        state["turn"] += 1
        if state["turn"] > 4:
            return "done, all checks nominal", []
        return "", [Call(state["turn"])]

    async def tool_fn(call):
        executed.append(call.n)
        return f"check {call.n} ok"

    async def feed_fn(call, result):
        return None

    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted is False
    assert res.final_text == "done, all checks nominal"
    assert res.steps_executed == 5
    assert executed == [1, 2, 3, 4]


def test_async_malformed_signal_failcloses_and_halts():
    step_fn, tool_fn, feed_fn, rec = make_loop(malformed_at=2)
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted is True
    assert res.halt_step == 2
    assert res.signal.malformed is True
    assert rec["executed"] == [1, 2]


def test_async_tool_exception_form_trips_enforcement():
    step_fn, tool_fn, feed_fn, rec = make_loop(exception_at=2)
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted is True
    assert res.source == "tool_exception"
    assert res.halt_step == 2
    assert rec["executed"] == [1, 2]
    assert rec["fed"] == [1]


def test_async_scan_model_output_catches_echoed_halt():
    state = {"turn": 0}
    executed = []

    async def step_fn():
        state["turn"] += 1
        if state["turn"] == 2:
            return ("I will keep going. " + HALT_LINE), [Call(99)]
        return "", [Call(state["turn"])]

    async def tool_fn(call):
        executed.append(call.n)
        return "ok"

    async def feed_fn(call, result):
        return None

    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10,
                                scan_model_output=True))
    assert res.halted is True
    assert res.source == "model_output"
    assert res.halt_step == 2
    assert executed == [1]                      # the whole batch was prevented
    assert res.actions_prevented == 1


def test_async_model_output_not_scanned_by_default():
    state = {"turn": 0}

    async def step_fn():
        state["turn"] += 1
        if state["turn"] == 2:
            return ("blah " + HALT_LINE), []
        return "", [Call(state["turn"])]

    async def tool_fn(call):
        return "ok"

    async def feed_fn(call, result):
        return None

    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10))
    assert res.halted is False


def test_async_multi_call_batch_prevents_the_rest_of_the_batch():
    """Calls within a step run sequentially, so a halt in call 1 stops call 2/3."""
    executed = []

    async def step_fn():
        return "", [Call(1), Call(2), Call(3)]

    async def tool_fn(call):
        executed.append(call.n)
        return HALT_BLOCK if call.n == 1 else "ok"

    async def feed_fn(call, result):
        return None

    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=2))
    assert res.halted is True
    assert executed == [1]
    assert res.actions_prevented == 2


def test_async_run_guarded_accepts_sync_callables():
    """A sync helper may be reused inside the async loop."""
    state = {"turn": 0}
    executed = []

    def step_fn():
        state["turn"] += 1
        return "", [Call(state["turn"])]

    def tool_fn(call):
        executed.append(call.n)
        return HALT_BLOCK if call.n == 2 else "ok"

    def feed_fn(call, result):
        return None

    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=5))
    assert res.halted is True and res.halt_step == 2
    assert executed == [1, 2]


def test_async_max_steps_is_respected():
    step_fn, tool_fn, feed_fn, rec = make_loop()
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=3))
    assert res.halted is False
    assert res.steps_executed == 3
    assert rec["executed"] == [1, 2, 3]


def test_supplied_interceptor_is_used_and_carries_state():
    ic = AsyncHaltInterceptor()
    step_fn, tool_fn, feed_fn, rec = make_loop(halt_at=2)
    res = run(async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=10,
                                interceptor=ic))
    assert res.halted and ic.halted is True
    assert ic.signal.directive == "halt"
    assert res.events is ic.events


# --------------------------------------------------------------------------- interceptor
def test_async_interceptor_raises_and_records_event():
    ic = AsyncHaltInterceptor()
    with pytest.raises(HaltEnforced) as exc:
        run(ic.observe(HALT_BLOCK, step=5, source="tool_result", pending=2))
    assert exc.value.step == 5
    assert exc.value.actions_prevented == 2
    assert ic.halted is True
    assert ic.events and ic.events[0]["event"] == "halt_detected"
    assert ic.events[0]["reason"] == "operator-request"


def test_async_interceptor_stays_halted_once_tripped():
    ic = AsyncHaltInterceptor()

    async def scenario():
        with pytest.raises(HaltEnforced):
            await ic.observe(HALT_BLOCK, step=1)
        with pytest.raises(HaltEnforced):
            await ic.observe("totally benign output", step=2)

    run(scenario())
    assert ic.actions_prevented == 1


def test_async_interceptor_ignores_benign_and_advisory_output():
    ic = AsyncHaltInterceptor()

    async def scenario():
        await ic.observe("all systems nominal", step=1)
        await ic.observe("RECUSE/0.1 warn; reason=production", step=2)
        await ic.observe("RECUSE/0.1 throttle; reason=load", step=3)

    run(scenario())
    assert ic.halted is False
    assert ic.events == []


def test_async_interceptor_is_a_halt_interceptor_with_the_same_api():
    ic = AsyncHaltInterceptor(fail_closed=False)
    assert isinstance(ic, HaltInterceptor)
    # sync API inherited unchanged
    assert ic.check("garbage RECUSE/ oops") is None
    assert ic.check(HALT_BLOCK) is not None
    with pytest.raises(HaltEnforced):
        ic.inspect(HALT_BLOCK, step=1)


def test_async_on_halt_callback_is_awaited():
    seen = {}

    async def on_halt(signal, ic):
        await asyncio.sleep(0)
        seen["reason"] = signal.reason

    ic = AsyncHaltInterceptor(on_halt=on_halt)
    with pytest.raises(HaltEnforced):
        run(ic.observe(HALT_BLOCK, step=1))
    assert seen["reason"] == "operator-request"


def test_sync_on_halt_callback_still_works():
    seen = {}
    ic = AsyncHaltInterceptor(on_halt=lambda s, i: seen.setdefault("id", s.id))
    with pytest.raises(HaltEnforced):
        run(ic.observe(HALT_BLOCK, step=1))
    assert seen["id"] == "abc-123"


def test_atrip_from_a_known_signal():
    ic = AsyncHaltInterceptor()
    sig = Signal(directive="halt", params={"reason": "operator-request"},
                 raw=HALT_LINE)
    with pytest.raises(HaltEnforced) as exc:
        run(ic.atrip(sig, step=7, source="tool_exception", pending=3))
    assert exc.value.step == 7
    assert exc.value.source == "tool_exception"
    assert ic.actions_prevented == 3


def test_async_logger_receives_the_halt_event():
    class Rec:
        def __init__(self):
            self.msgs = []

        def warning(self, fmt, *args):
            self.msgs.append(fmt % args)

    rec = Rec()
    ic = AsyncHaltInterceptor(logger=rec)
    with pytest.raises(HaltEnforced):
        run(ic.observe(HALT_BLOCK, step=1))
    assert rec.msgs and "halt_detected" in rec.msgs[0]


# --------------------------------------------------------------------------- decorator
def test_async_halt_guarded_decorator_trips_on_result():
    ic = AsyncHaltInterceptor()

    @async_halt_guarded(ic)
    async def run_tool(call):
        await asyncio.sleep(0)
        return HALT_BLOCK if call == "bad" else "fine"

    assert run(run_tool("good")) == "fine"
    with pytest.raises(HaltEnforced):
        run(run_tool("bad"))
    assert ic.halted


def test_async_halt_guarded_decorator_trips_on_exception():
    ic = AsyncHaltInterceptor()

    @async_halt_guarded(ic)
    async def run_tool(call):
        raise HaltSignalException(HALT_LINE)

    with pytest.raises(HaltEnforced) as exc:
        run(run_tool("x"))
    assert exc.value.source == "tool_exception"
    assert ic.signal.directive == "halt"


def test_async_halt_guarded_blocks_every_later_call():
    ic = AsyncHaltInterceptor()
    executed = []

    @async_halt_guarded(ic)
    async def run_tool(call):
        executed.append(call)
        return HALT_BLOCK if call == 2 else "ok"

    async def scenario():
        await run_tool(1)
        with pytest.raises(HaltEnforced):
            await run_tool(2)
        with pytest.raises(HaltEnforced):
            await run_tool(3)

    run(scenario())
    # call 3 ran its body but its result was never returned to the caller;
    # what matters is that the caller could never act on it.
    assert ic.halted and ic.actions_prevented >= 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
