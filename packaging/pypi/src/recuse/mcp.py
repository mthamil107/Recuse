"""RECUSE halt enforcement for MCP (Model Context Protocol) tool calls.

MCP is where an agent's tool calls actually happen, which makes it the right
place to enforce a RECUSE ``halt``: a governed server can put a stop sentinel in
any tool result, and the *client harness* — not the model — must act on it.

This module is deliberately written against MCP's *shapes*, not its Python
package. Nothing here imports ``mcp`` at module import time; the real package is
only ever imported lazily inside :func:`install`, and even there it is optional.
The package therefore adds **zero required runtime dependencies** and the whole
module is importable and testable without MCP installed.

Recognized result shapes (see :func:`extract_text`)::

    "RECUSE/0.2 halt; ..."                                  # a plain string
    b"RECUSE/0.2 halt; ..."                                 # bytes
    {"content": [{"type": "text", "text": "RECUSE/..."}]}   # CallToolResult-ish dict
    {"content": [...], "isError": True}                     # error results too
    {"structuredContent": {...}}                            # structured output
    [{"type": "text", "text": "..."}, ...]                  # a bare block list
    obj.content -> [obj.text, ...]                          # pydantic-style objects

Detection is the package's usual *fail-closed* scan (:func:`recuse.detect_stop`):
``halt``/``deny``, an unknown directive, or a malformed ``RECUSE/`` fragment all
stop the agent; ``warn``/``throttle`` do not. Detection is case-sensitive on the
literal ``RECUSE/`` token, so a policy URL such as
``https://github.com/mthamil107/Recuse`` never false-trips it.

Quickstart::

    from recuse.mcp import RecuseMCPMiddleware
    from recuse import HaltEnforced

    guarded = RecuseMCPMiddleware(session.call_tool)
    try:
        result = guarded("read_file", {"path": "/etc/hosts"})
    except HaltEnforced as stop:
        ...  # the loop is over; no further tool call can run

Async is identical via :meth:`RecuseMCPMiddleware.acall` or
:func:`wrap_async_call_tool`.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, List, Optional

from .halt import HaltEnforced, HaltInterceptor, detect_stop
from .signal import Signal, _coerce_text

__all__ = [
    "extract_text",
    "detect_stop_in_result",
    "guard_tool_result",
    "RecuseMCPMiddleware",
    "wrap_call_tool",
    "wrap_async_call_tool",
    "install",
    "MCP_SOURCE",
]

#: The ``source`` label recorded on :class:`~recuse.HaltEnforced` / interceptor
#: events raised from this module.
MCP_SOURCE = "mcp_tool_result"

# Attributes searched, in order, for text on an MCP content block / result object.
_TEXT_ATTRS = ("text", "data", "message", "value")
# Attributes searched, in order, for nested content on a result object.
_CONTENT_ATTRS = ("content", "structuredContent", "structured_content",
                  "structured_output", "result", "output", "toolResult")
# Dict keys searched, in order, for nested content on a result mapping.
_CONTENT_KEYS = ("content", "structuredContent", "structured_content",
                 "structured_output", "result", "output", "toolResult",
                 "tool_result", "error")
# Dict keys that directly carry text.
_TEXT_KEYS = ("text", "data", "message", "value", "reason", "detail")

_MAX_DEPTH = 6


def _walk(value: Any, out: List[str], depth: int, seen: set) -> None:
    """Collect scannable text from an arbitrary MCP-ish value into ``out``."""
    if value is None or depth > _MAX_DEPTH:
        return
    if isinstance(value, str):
        if value:
            out.append(value)
        return
    if isinstance(value, bytes):
        out.append(value.decode("utf-8", "replace"))
        return
    if isinstance(value, (bool, int, float)):
        return
    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)

    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            if key in value:
                _walk(value[key], out, depth + 1, seen)
        for key in _CONTENT_KEYS:
            if key in value:
                _walk(value[key], out, depth + 1, seen)
        # Anything we did not name explicitly still gets scanned, so a sentinel
        # tucked into a vendor-specific field is not missed.
        for key, item in value.items():
            if key in _TEXT_KEYS or key in _CONTENT_KEYS:
                continue
            _walk(item, out, depth + 1, seen)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _walk(item, out, depth + 1, seen)
        return

    # Duck-typed objects: pydantic ``TextContent`` / ``CallToolResult`` and friends.
    handled = False
    for attr in _TEXT_ATTRS:
        item = getattr(value, attr, None)
        if isinstance(item, (str, bytes)):
            _walk(item, out, depth + 1, seen)
            handled = True
    for attr in _CONTENT_ATTRS:
        item = getattr(value, attr, None)
        if item is not None and not callable(item):
            _walk(item, out, depth + 1, seen)
            handled = True
    if handled:
        return

    # Last resort: a JSON/str rendering of the object (same coercion the rest of
    # the package uses), so an unknown shape still gets scanned.
    text = _coerce_text(value)
    if text:
        out.append(text)


def extract_text(result: Any) -> str:
    """Render an MCP tool result to a single scannable blob of text.

    Handles plain strings/bytes, ``CallToolResult``-shaped dicts
    (``{"content": [{"type": "text", "text": ...}], "isError": ...}``), bare
    lists of content blocks, pydantic-style objects exposing ``.content`` /
    ``.text``, and arbitrary nested combinations of those. Unknown shapes fall
    back to the package's JSON coercion so nothing escapes the scan.
    """
    out: List[str] = []
    _walk(result, out, 0, set())
    if not out:
        return _coerce_text(result)
    return "\n".join(out)


def detect_stop_in_result(result: Any, *, fail_closed: bool = True) -> Optional[Signal]:
    """Return a stop :class:`~recuse.Signal` carried by an MCP tool result, else ``None``.

    Non-raising counterpart to :func:`guard_tool_result`. Advisory signals
    (``warn``/``throttle``) return ``None``.
    """
    return detect_stop(extract_text(result), fail_closed=fail_closed)


def guard_tool_result(result: Any,
                      interceptor: Optional[HaltInterceptor] = None,
                      *,
                      tool_name: Optional[str] = None,
                      step: Optional[int] = None,
                      pending: int = 0,
                      fail_closed: bool = True) -> Any:
    """Inspect one MCP tool result and enforce a RECUSE stop directive.

    Args:
        result: the MCP tool result, in any shape :func:`extract_text` handles.
        interceptor: an optional :class:`~recuse.HaltInterceptor` to record the
            trip on (so state and events are shared across a whole session). If
            omitted, a stateless check is performed.
        tool_name: name of the tool that produced ``result``; recorded in the
            ``source`` label for forensics.
        step: loop step number, for the interceptor's event log.
        pending: number of tool calls in this batch that had not yet run, counted
            as prevented on a trip.
        fail_closed: treat a malformed ``RECUSE/`` fragment as a stop (default).
            Ignored when ``interceptor`` is given (it carries its own policy).

    Returns:
        ``result`` unchanged when no stop directive is present.

    Raises:
        HaltEnforced: the instant a stop directive is found. Let it propagate —
            that propagation *is* the enforcement.
    """
    source = MCP_SOURCE if not tool_name else "{0}:{1}".format(MCP_SOURCE, tool_name)
    text = extract_text(result)
    if interceptor is None:
        signal = detect_stop(text, fail_closed=fail_closed)
        if signal is not None:
            raise HaltEnforced(signal, step=0 if step is None else step,
                               source=source, actions_prevented=pending)
        return result
    interceptor.inspect(text, step=step, source=source, pending=pending)
    return result


class RecuseMCPMiddleware:
    """Wrap an MCP ``call_tool(name, args)`` callable with halt enforcement.

    Every result is scanned before it is returned to the caller, and once a stop
    directive has been seen the middleware refuses to invoke the tool at all —
    so a halt delivered by one MCP server stops calls to *every* server in the
    session, not just the one that sent it.

    The wrapped callable may be sync (call via :meth:`__call__` / :meth:`call`)
    or async (call via :meth:`acall`); use :meth:`wrap` / :meth:`wrap_async` to
    produce a drop-in replacement callable instead.

    Args:
        call_tool: the underlying ``call_tool(name, args, ...)`` callable. May be
            omitted if you only use :meth:`guard` / :meth:`wrap`.
        interceptor: an existing :class:`~recuse.HaltInterceptor` to share across
            the session. One is created if omitted.
        scan_args: also scan the *outgoing* tool arguments, catching a sentinel
            that a compromised model tried to launder through a tool call.

    Example::

        guarded = RecuseMCPMiddleware(session.call_tool)
        result = guarded("search", {"q": "..."})     # raises HaltEnforced on a halt
    """

    def __init__(self,
                 call_tool: Optional[Callable[..., Any]] = None,
                 interceptor: Optional[HaltInterceptor] = None,
                 *,
                 scan_args: bool = False,
                 fail_closed: bool = True):
        self.call_tool = call_tool
        self.interceptor = interceptor or HaltInterceptor(fail_closed=fail_closed)
        self.scan_args = scan_args
        self.calls_made = 0
        self.calls_prevented = 0

    # -- state ------------------------------------------------------------------
    @property
    def halted(self) -> bool:
        """True once a stop directive has been enforced in this session."""
        return self.interceptor.halted

    @property
    def signal(self) -> Optional[Signal]:
        """The stop :class:`~recuse.Signal` that tripped this session, if any."""
        return self.interceptor.signal

    # -- enforcement ------------------------------------------------------------
    def _preflight(self, name: Optional[str], args: Any) -> None:
        """Refuse to run anything once halted; optionally scan outgoing args."""
        if self.interceptor.halted:
            self.calls_prevented += 1
            # Re-raises HaltEnforced and counts the prevented action.
            self.interceptor.inspect("", source=MCP_SOURCE)
        if self.scan_args and args is not None:
            self.interceptor.inspect(
                extract_text(args),
                source="mcp_tool_args" if not name else "mcp_tool_args:{0}".format(name))

    def guard(self, result: Any, *, tool_name: Optional[str] = None,
              pending: int = 0) -> Any:
        """Enforce a stop directive on an already-obtained MCP result."""
        return guard_tool_result(result, self.interceptor, tool_name=tool_name,
                                 pending=pending)

    # -- invocation -------------------------------------------------------------
    def call(self, name: str, args: Any = None, *extra: Any, **kwargs: Any) -> Any:
        """Invoke the wrapped sync ``call_tool`` and enforce the result."""
        if self.call_tool is None:
            raise RuntimeError(
                "RecuseMCPMiddleware has no call_tool; pass one to __init__ or "
                "use wrap()/guard()")
        return self.wrap(self.call_tool)(name, args, *extra, **kwargs)

    __call__ = call

    async def acall(self, name: str, args: Any = None, *extra: Any,
                    **kwargs: Any) -> Any:
        """Invoke the wrapped async ``call_tool`` and enforce the result."""
        if self.call_tool is None:
            raise RuntimeError(
                "RecuseMCPMiddleware has no call_tool; pass one to __init__ or "
                "use wrap_async()/guard()")
        return await self.wrap_async(self.call_tool)(name, args, *extra, **kwargs)

    # -- wrappers ---------------------------------------------------------------
    def wrap(self, call_tool: Callable[..., Any]) -> Callable[..., Any]:
        """Return a sync ``call_tool``-shaped callable with enforcement applied."""
        middleware = self

        @functools.wraps(call_tool)
        def guarded(name: str, args: Any = None, *extra: Any, **kwargs: Any) -> Any:
            middleware._preflight(name, args)
            result = call_tool(name, args, *extra, **kwargs)
            middleware.calls_made += 1
            return middleware.guard(result, tool_name=name)

        return guarded

    def wrap_async(self, call_tool: Callable[..., Any]) -> Callable[..., Any]:
        """Return an async ``call_tool``-shaped coroutine function with enforcement."""
        middleware = self

        @functools.wraps(call_tool)
        async def guarded(name: str, args: Any = None, *extra: Any,
                          **kwargs: Any) -> Any:
            middleware._preflight(name, args)
            result = await call_tool(name, args, *extra, **kwargs)
            middleware.calls_made += 1
            return middleware.guard(result, tool_name=name)

        return guarded

    #: Alias matching the module-level helper name.
    wrap_async_call_tool = wrap_async


def wrap_call_tool(call_tool: Callable[..., Any],
                   interceptor: Optional[HaltInterceptor] = None,
                   **kwargs: Any) -> Callable[..., Any]:
    """Wrap a sync MCP ``call_tool(name, args)`` callable with halt enforcement.

    Convenience over :class:`RecuseMCPMiddleware`; the middleware instance is
    reachable as ``wrapped.recuse_middleware``.
    """
    middleware = RecuseMCPMiddleware(call_tool, interceptor, **kwargs)
    guarded = middleware.wrap(call_tool)
    guarded.recuse_middleware = middleware  # type: ignore[attr-defined]
    return guarded


def wrap_async_call_tool(call_tool: Callable[..., Any],
                         interceptor: Optional[HaltInterceptor] = None,
                         **kwargs: Any) -> Callable[..., Any]:
    """Wrap an async MCP ``call_tool(name, args)`` coroutine function.

    The returned coroutine function awaits the underlying call, then enforces any
    RECUSE stop directive in the result (raising :class:`~recuse.HaltEnforced`).
    Once halted it raises *before* awaiting, so no further tool call is issued.
    The middleware instance is reachable as ``wrapped.recuse_middleware``.
    """
    middleware = RecuseMCPMiddleware(call_tool, interceptor, **kwargs)
    guarded = middleware.wrap_async(call_tool)
    guarded.recuse_middleware = middleware  # type: ignore[attr-defined]
    return guarded


def _is_async_callable(fn: Any) -> bool:
    import inspect as _inspect

    if _inspect.iscoroutinefunction(fn):
        return True
    call = getattr(fn, "__call__", None)
    return call is not None and _inspect.iscoroutinefunction(call)


def install(server_or_client: Any,
            interceptor: Optional[HaltInterceptor] = None,
            *,
            attr: Optional[str] = None,
            **kwargs: Any) -> RecuseMCPMiddleware:
    """Best-effort, duck-typed installation of halt enforcement on an MCP object.

    What this supports: **any object that exposes a callable tool-invocation
    attribute which can be reassigned**. The attribute is discovered by name, in
    order — ``call_tool``, ``callTool``, ``call_tool_async``, ``invoke_tool``,
    ``run_tool`` — or you can name it explicitly with ``attr``. Sync and async
    attributes are both handled (detected via :mod:`inspect`). In practice this
    covers an ``mcp.ClientSession``, a thin client wrapper of your own, or a test
    double.

    What this does **not** do: it does not hook a *server's* request-routing
    internals, does not patch classes (only the instance you pass), and cannot
    help if the attribute is read-only (a frozen dataclass, a ``__slots__``
    object, or a plain method resolved off the class). In those cases wrap the
    callable yourself with :func:`wrap_call_tool` / :func:`wrap_async_call_tool`
    and pass the wrapper wherever the original went — enforcement is identical.

    The real ``mcp`` package is imported lazily *inside this function* and only to
    enrich the error message when discovery fails; it is never required.

    Returns:
        The :class:`RecuseMCPMiddleware` now installed, for inspecting
        ``.halted`` / ``.signal`` after the fact.

    Raises:
        TypeError: no wrappable attribute was found, or it could not be replaced.
    """
    candidates = (attr,) if attr else (
        "call_tool", "callTool", "call_tool_async", "invoke_tool", "run_tool")

    for name in candidates:
        if not name:
            continue
        original = getattr(server_or_client, name, None)
        if original is None or not callable(original):
            continue
        middleware = RecuseMCPMiddleware(original, interceptor, **kwargs)
        if _is_async_callable(original):
            guarded = middleware.wrap_async(original)
        else:
            guarded = middleware.wrap(original)
        guarded.recuse_middleware = middleware  # type: ignore[attr-defined]
        guarded.recuse_original = original  # type: ignore[attr-defined]
        try:
            setattr(server_or_client, name, guarded)
        except (AttributeError, TypeError) as exc:
            raise TypeError(
                "recuse.mcp.install: {0!r}.{1} could not be replaced ({2}); wrap "
                "the callable with recuse.mcp.wrap_call_tool() instead".format(
                    type(server_or_client).__name__, name, exc))
        return middleware

    try:  # lazy, optional: only to make the failure message more useful
        import mcp  # noqa: F401
        hint = ("the mcp package is installed, but this object exposes no "
                "recognized tool-call attribute")
    except Exception:
        hint = ("the mcp package is not installed; if this is a custom client, "
                "pass attr=<name>")
    raise TypeError(
        "recuse.mcp.install: no wrappable tool-call attribute on {0!r} ({1}). "
        "Tried: {2}".format(type(server_or_client).__name__, hint,
                            ", ".join(c for c in candidates if c)))
