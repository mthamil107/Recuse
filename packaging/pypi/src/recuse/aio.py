"""Async halt enforcement — :mod:`recuse.halt` for ``async def`` agent loops.

Modern agent loops are coroutines: the model turn is awaited, tool calls are
awaited (often concurrently), and results are fed back through an async channel.
The synchronous :class:`~recuse.halt.HaltInterceptor` cannot sit in that loop
without blocking it, so this module mirrors it coroutine-for-coroutine:

    :class:`~recuse.halt.HaltInterceptor` -> :class:`AsyncHaltInterceptor`
    :meth:`~recuse.halt.HaltInterceptor.inspect` -> :meth:`AsyncHaltInterceptor.observe`
    :func:`~recuse.halt.run_guarded`  -> :func:`async_run_guarded`
    :func:`~recuse.halt.halt_guarded` -> :func:`async_halt_guarded`

The enforcement semantics are identical and deliberately unchanged: on the first
detected stop signal a :class:`~recuse.halt.HaltEnforced` exception propagates out
of the loop, so no further tool call and no further model turn can run. The agent
never gets the chance to "decide" to keep going.

Detection is NOT reimplemented here — :func:`~recuse.halt.detect_stop` and
:func:`~recuse.signal.parse_signal` remain the single parsing path.

:class:`AsyncHaltInterceptor` subclasses the sync interceptor, so its whole API
(``check``, ``inspect``, ``trip``, ``halted``, ``events``, …) is available
unchanged; ``observe``/``atrip`` are the awaitable additions, and they will await
an ``on_halt`` callback that happens to be a coroutine function.

Stdlib only (``asyncio`` is not imported — nothing here needs an event loop of
its own; the caller's loop drives everything).
"""
from __future__ import annotations

import functools
import inspect as _inspect
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from .halt import (
    HaltEnforced,
    HaltInterceptor,
    HaltSignalException,
    LoopResult,
    detect_stop,
)
from .signal import Signal

__all__ = [
    "AsyncHaltInterceptor",
    "async_run_guarded",
    "async_halt_guarded",
    "detect_stop",
]


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it.

    Lets every hook (``step_fn``/``tool_fn``/``feed_fn``/``on_halt``) be either a
    coroutine function or a plain one, so an async loop can reuse sync helpers.
    """
    if _inspect.isawaitable(value):
        return await value
    return value


class AsyncHaltInterceptor(HaltInterceptor):
    """Halt guard for an ``async`` agent loop.

    Same constructor, same state, same events as :class:`~recuse.halt.HaltInterceptor`
    — see that class for the arguments. The difference is :meth:`observe`, the
    awaitable counterpart of :meth:`~recuse.halt.HaltInterceptor.inspect`, which
    additionally awaits an async ``on_halt`` callback.
    """

    # -- detection ---------------------------------------------------------------
    async def observe(self, text: Any, *, step: Optional[int] = None,
                      source: str = "tool_result", pending: int = 0) -> None:
        """Scan one piece of text. Raises :class:`~recuse.halt.HaltEnforced` on a halt.

        The awaitable mirror of :meth:`~recuse.halt.HaltInterceptor.inspect`,
        with identical arguments and identical stop semantics: once tripped, the
        interceptor lets nothing through, and every later ``observe`` re-raises
        while counting the action it prevented.
        """
        step = self.current_step if step is None else step
        if self.halted:  # defensive: never let anything through after a halt
            self.actions_prevented += 1 + pending
            raise HaltEnforced(self.signal, step=self.halt_step, source=self.source,
                               actions_prevented=self.actions_prevented)
        signal = self.check(text)  # -> detect_stop -> parse_signal (not duplicated)
        if signal is not None:
            await self.atrip(signal, step=step, source=source, pending=pending)

    async def atrip(self, signal: Signal, *, step: Optional[int] = None,
                    source: str = "tool_exception", pending: int = 0) -> None:
        """Force a trip from an already-known signal, awaiting an async ``on_halt``.

        The awaitable mirror of :meth:`~recuse.halt.HaltInterceptor.trip` (which
        remains available and is fine when ``on_halt`` is synchronous).
        """
        step = self.current_step if step is None else step
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
            await _maybe_await(self.on_halt(signal, self))
        raise HaltEnforced(signal, step=step, source=source,
                           actions_prevented=self.actions_prevented)


# --------------------------------------------------------------------------- decorator
def async_halt_guarded(interceptor: AsyncHaltInterceptor):
    """Decorate an async tool-executor ``async f(call, ...) -> result``.

    The awaitable mirror of :func:`~recuse.halt.halt_guarded`: the wrapped
    coroutine runs the tool, then hands its result to the interceptor, so a halt
    in the result raises :class:`~recuse.halt.HaltEnforced` before the caller can
    act on it. A tool raising :class:`~recuse.halt.HaltSignalException` trips too.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await _maybe_await(fn(*args, **kwargs))
            except HaltSignalException as e:
                await interceptor.atrip(e.signal, source="tool_exception")
                raise  # unreachable: atrip() raised HaltEnforced
            await interceptor.observe(result, source="tool_result")
            return result

        return wrapper

    return decorator


# --------------------------------------------------------------------------- driver
async def async_run_guarded(
    step_fn: Callable[[], Awaitable[Tuple[str, list]]],
    tool_fn: Callable[[Any], Awaitable[Any]],
    feed_fn: Callable[[Any, Any], Awaitable[None]],
    *, max_steps: int = 8,
    interceptor: Optional[AsyncHaltInterceptor] = None,
    scan_model_output: bool = False,
) -> LoopResult:
    """Run a guarded **async** agent loop; stop the instant a halt is enforced.

    The awaitable mirror of :func:`~recuse.halt.run_guarded`, returning the same
    :class:`~recuse.halt.LoopResult`. Each callable may be a coroutine function
    or a plain function:

        ``await step_fn()`` -> ``(text, calls)``. One model turn; empty ``calls``
            means the agent is done.
        ``await tool_fn(call)`` -> ``result``. Execute one tool call. May raise
            :class:`~recuse.halt.HaltSignalException` to deliver a halt.
        ``await feed_fn(call, result)``. Feed the result back to the model.

    Tool calls within a step are executed **sequentially**, on purpose: a halt in
    call *i* must prevent call *i+1*. Gathering them concurrently would let the
    very actions the halt forbids run before it is seen.
    """
    ic = interceptor or AsyncHaltInterceptor(scan_model_output=scan_model_output)
    if scan_model_output:
        ic.scan_model_output = True
    steps_executed = 0
    tools_executed = 0
    final_text = ""
    events: List[dict] = ic.events
    try:
        for step_idx in range(1, max_steps + 1):
            ic.current_step = step_idx
            text, calls = await _maybe_await(step_fn())
            steps_executed += 1
            calls = list(calls or [])
            if ic.scan_model_output and text:
                # A halt echoed in the model's own output prevents this whole batch.
                await ic.observe(text, step=step_idx, source="model_output",
                                 pending=len(calls))
            if not calls:
                final_text = text or ""
                break
            for i, call in enumerate(calls):
                pending_after = len(calls) - i - 1
                try:
                    result = await _maybe_await(tool_fn(call))
                except HaltSignalException as e:
                    # The tool raised. Nothing executed after this point.
                    await ic.atrip(e.signal, step=step_idx, source="tool_exception",
                                   pending=pending_after)
                tools_executed += 1
                await ic.observe(result, step=step_idx, source="tool_result",
                                 pending=pending_after)
                await _maybe_await(feed_fn(call, result))
    except HaltEnforced as e:
        return LoopResult(
            halted=True, signal=ic.signal, halt_step=e.step, source=e.source,
            actions_prevented=ic.actions_prevented, steps_executed=steps_executed,
            tools_executed=tools_executed, final_text=final_text, events=events)
    return LoopResult(
        halted=False, signal=None, halt_step=None, source=None, actions_prevented=0,
        steps_executed=steps_executed, tools_executed=tools_executed,
        final_text=final_text, events=events)
