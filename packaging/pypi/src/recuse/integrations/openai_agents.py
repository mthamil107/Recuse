"""OpenAI Agents SDK / plain OpenAI tool-loop adapter.

Three entry points, in increasing order of how much of your loop they own:

1. :func:`guard_tool_output` — call it on the return value of any tool you
   execute yourself. Benign output is returned unchanged; a halt raises.
2. :func:`wrap_tool` — a decorator that does (1) around an existing tool
   function, sync or async, preserving its name, docstring and signature so the
   OpenAI SDK's schema generation still sees the original function.
3. :func:`guard_messages` — scan a whole ``messages`` array (or an Agents SDK
   item list) before feeding it back to the model, catching halts that arrived
   through tools you did not wrap.

Plus :class:`RecuseRunHooks`, a duck-typed ``RunHooks`` implementation for
``Runner.run(..., hooks=...)`` in the OpenAI Agents SDK.

**``openai`` is never imported here.** Everything is written against the message
and item *shapes* — ``{"role": "tool", "content": ...}`` for Chat Completions,
``{"type": "function_call_output", "output": ...}`` for the Responses/Agents item
protocol — so this module imports and tests with the SDK absent.

Enforcement is by exception: :class:`recuse.HaltEnforced` propagates out of your
loop, so no further tool call and no further model turn can execute.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Callable, Optional

from ..halt import HaltInterceptor
from . import _guard_text

__all__ = [
    "guard_tool_output",
    "guard_messages",
    "guard_response",
    "wrap_tool",
    "RecuseRunHooks",
]

#: Message roles whose content is tool-provided (and therefore attacker- or
#: server-controlled) — the channel a RECUSE signal actually rides.
_TOOL_ROLES = frozenset({"tool", "function"})

#: Agents SDK / Responses API item types carrying tool output.
_TOOL_ITEM_TYPES = frozenset({
    "function_call_output",
    "tool_result",
    "computer_call_output",
    "local_shell_call_output",
    "mcp_call",
})


def guard_tool_output(output: Any, *,
                      interceptor: Optional[HaltInterceptor] = None,
                      fail_closed: bool = True,
                      step: Optional[int] = None,
                      source: str = "tool_result",
                      pending: int = 0) -> Any:
    """Scan one tool result and return it unchanged, or raise on a halt.

    Args:
        output: whatever the tool returned — ``str``, ``bytes``, ``dict``, a
            JSON-serializable structure, or an SDK result object. All shapes are
            coerced to text before scanning, so a sentinel buried in any field
            is still seen.
        interceptor: optional shared :class:`recuse.HaltInterceptor` to record
            the halt event on before raising.
        fail_closed: treat malformed ``RECUSE/`` fragments and unknown
            directives as stops (default).
        pending: tool calls in the current batch not yet executed; counted as
            prevented actions on the interceptor.

    Returns:
        ``output``, unmodified, when nothing stop-worthy is present.

    Raises:
        recuse.HaltEnforced: a ``halt``/``deny`` (or, fail-closed, malformed)
            sentinel was present. Let it propagate — that is the enforcement.
    """
    _guard_text(output, interceptor=interceptor, fail_closed=fail_closed,
                step=step, source=source, pending=pending)
    return output


def _item_is_tool_output(item: Any) -> bool:
    """True if ``item`` looks like a tool-authored message/item worth scanning."""
    if isinstance(item, dict):
        role = item.get("role")
        kind = item.get("type")
    else:
        role = getattr(item, "role", None)
        kind = getattr(item, "type", None)
    if role is not None and str(role).lower() in _TOOL_ROLES:
        return True
    if kind is not None and str(kind).lower() in _TOOL_ITEM_TYPES:
        return True
    # Unrecognized shape: scan it rather than skip it. Fail closed.
    return role is None and kind is None


def guard_messages(messages: Any, *,
                   interceptor: Optional[HaltInterceptor] = None,
                   fail_closed: bool = True,
                   step: Optional[int] = None,
                   scan_all_roles: bool = False) -> Any:
    """Scan a ``messages``/items array, raising on a halt in any tool content.

    Only tool-authored entries are inspected by default: ``role`` in
    ``{"tool", "function"}`` or an Agents SDK tool-output ``type``. Entries whose
    shape cannot be classified are scanned anyway (fail-closed) — an unreadable
    message must never be a way to smuggle a halt past the guard.

    Args:
        scan_all_roles: also scan ``user``/``assistant``/``system`` content.
            Off by default: model and user text is freely echoable, so stopping
            on it is a policy decision rather than signal handling.

    Returns:
        ``messages``, unmodified, when no stop signal is present.
    """
    if messages is None:
        return messages
    items = messages if isinstance(messages, (list, tuple)) else [messages]
    for item in items:
        if scan_all_roles or _item_is_tool_output(item):
            _guard_text(item, interceptor=interceptor, fail_closed=fail_closed,
                        step=step, source="tool_result")
    return messages


def guard_response(response: Any, *,
                   interceptor: Optional[HaltInterceptor] = None,
                   fail_closed: bool = True,
                   step: Optional[int] = None) -> Any:
    """Scan a model response object (or an Agents SDK ``RunResult``) for a halt.

    Covers the case where the halt was already folded into the transcript — e.g.
    ``result.new_items`` on an Agents SDK run — before you act on the result.
    """
    _guard_text(response, interceptor=interceptor, fail_closed=fail_closed,
                step=step, source="model_output")
    return response


def wrap_tool(fn: Optional[Callable] = None, *,
              interceptor: Optional[HaltInterceptor] = None,
              fail_closed: bool = True,
              source: str = "tool_result") -> Callable:
    """Decorate a tool function so its return value is halt-scanned.

    Usable bare or with arguments, and on sync or async functions::

        @wrap_tool
        def read_file(path: str) -> str:
            ...

        @wrap_tool(interceptor=shared_interceptor)
        async def http_get(url: str) -> str:
            ...

    :func:`functools.wraps` is applied, so ``__name__``, ``__doc__``,
    ``__wrapped__`` and the type annotations survive — the OpenAI SDK's
    ``function_tool``/schema generation still introspects the original function.
    Apply this decorator *below* ``@function_tool`` so it wraps the raw callable.
    """

    def decorator(target: Callable) -> Callable:
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = await target(*args, **kwargs)
                return guard_tool_output(result, interceptor=interceptor,
                                         fail_closed=fail_closed, source=source)

            return async_wrapper

        @functools.wraps(target)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = target(*args, **kwargs)
            return guard_tool_output(result, interceptor=interceptor,
                                     fail_closed=fail_closed, source=source)

        return wrapper

    if fn is None:
        return decorator
    return decorator(fn)


class RecuseRunHooks:
    """Duck-typed OpenAI Agents SDK ``RunHooks`` that enforce RECUSE ``halt``.

    Pass to ``Runner.run(agent, input, hooks=RecuseRunHooks())``. ``on_tool_end``
    raises :class:`recuse.HaltEnforced`, which propagates out of the runner and
    ends the run before the model sees the tool output.

    The hook methods are coroutines because the Agents SDK awaits them; the
    detection itself is synchronous. ``agents`` is never imported — the SDK only
    requires an object exposing these method names.
    """

    def __init__(self, interceptor: Optional[HaltInterceptor] = None, *,
                 fail_closed: bool = True, scan_model_output: bool = False):
        self.interceptor = interceptor
        self.fail_closed = fail_closed
        self.scan_model_output = scan_model_output
        self.inspected = 0

    async def on_tool_end(self, context: Any = None, agent: Any = None,
                          tool: Any = None, result: Any = None,
                          **kwargs: Any) -> None:
        """The enforcement point for an Agents SDK run."""
        self.inspected += 1
        guard_tool_output(result, interceptor=self.interceptor,
                          fail_closed=self.fail_closed)

    async def on_tool_start(self, context: Any = None, agent: Any = None,
                            tool: Any = None, **kwargs: Any) -> None:
        return None

    async def on_agent_start(self, context: Any = None, agent: Any = None,
                             **kwargs: Any) -> None:
        return None

    async def on_agent_end(self, context: Any = None, agent: Any = None,
                           output: Any = None, **kwargs: Any) -> None:
        if self.scan_model_output:
            guard_response(output, interceptor=self.interceptor,
                           fail_closed=self.fail_closed)

    async def on_handoff(self, context: Any = None, from_agent: Any = None,
                         to_agent: Any = None, **kwargs: Any) -> None:
        return None

    async def on_llm_start(self, context: Any = None, agent: Any = None,
                           **kwargs: Any) -> None:
        return None

    async def on_llm_end(self, context: Any = None, agent: Any = None,
                         response: Any = None, **kwargs: Any) -> None:
        if self.scan_model_output:
            guard_response(response, interceptor=self.interceptor,
                           fail_closed=self.fail_closed)

    @staticmethod
    def run_sync(coro: Any) -> Any:
        """Test/sync helper: drive one of the coroutine hooks to completion."""
        return asyncio.run(coro)

    def __repr__(self) -> str:
        return "RecuseRunHooks(fail_closed={0!r}, inspected={1!r})".format(
            self.fail_closed, self.inspected)
