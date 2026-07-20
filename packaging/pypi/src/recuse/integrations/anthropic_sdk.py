"""Anthropic Messages API / Claude Agent SDK adapter.

The Messages API returns tool results to the model as ``tool_result`` content
blocks inside a ``user`` message::

    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01...",
         "content": [{"type": "text", "text": "...RECUSE/0.2 halt; ..."}]}
    ]}

This module scans exactly that channel. Call :func:`guard_messages` on the array
you are about to send back to the model, :func:`guard_tool_result` on a single
block you just built, or decorate the tool itself with :func:`wrap_tool` so a
halt trips before the block is ever assembled. Any of them raises
:class:`recuse.HaltEnforced`, which propagates out of your agent loop and
guarantees no further tool call or model turn runs.

**``anthropic`` is never imported here.** Blocks are handled as dicts *or* as
SDK objects (``.type`` / ``.text`` / ``.content`` attributes), so the module
imports and tests with the SDK absent and adds no runtime dependency.

``tool_result.content`` may legitimately be a bare string, a list of text blocks,
or a list mixing text and image blocks; all three are handled, and any shape that
cannot be classified is scanned wholesale rather than skipped.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional

from ..halt import HaltInterceptor
from . import _guard_text

__all__ = [
    "guard_tool_result",
    "guard_content",
    "guard_messages",
    "guard_response",
    "wrap_tool",
    "is_tool_result_block",
    "build_halt_tool_result",
]

#: Content-block types that carry tool-authored (untrusted) text.
_TOOL_BLOCK_TYPES = frozenset({"tool_result", "mcp_tool_result"})


def _block_type(block: Any) -> Optional[str]:
    """Return a content block's ``type``, for dict and SDK-object blocks alike."""
    if isinstance(block, dict):
        value = block.get("type")
    else:
        value = getattr(block, "type", None)
    return None if value is None else str(value)


def _block_field(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def is_tool_result_block(block: Any) -> bool:
    """True if ``block`` is a ``tool_result`` content block (dict or SDK object)."""
    kind = _block_type(block)
    return kind is not None and kind.lower() in _TOOL_BLOCK_TYPES


def guard_tool_result(block: Any, *,
                      interceptor: Optional[HaltInterceptor] = None,
                      fail_closed: bool = True,
                      step: Optional[int] = None,
                      pending: int = 0) -> Any:
    """Scan a single ``tool_result`` block; return it unchanged or raise on a halt.

    The block's ``content`` is scanned whether it is a string, a list of text
    blocks, or a mixed list. Non-``tool_result`` blocks are scanned too when
    passed directly here, on the assumption that the caller meant to check them.

    Raises:
        recuse.HaltEnforced: a ``halt``/``deny`` (or, fail-closed, malformed)
            sentinel was present in the tool output.
    """
    payload = _block_field(block, "content")
    # Scan the whole block, not just ``content``: a sentinel can also ride the
    # ``is_error`` message or a vendor-specific sibling field.
    _guard_text(block if payload is None else [block, payload],
                interceptor=interceptor, fail_closed=fail_closed, step=step,
                source="tool_result", pending=pending)
    return block


def guard_content(content: Any, *,
                  interceptor: Optional[HaltInterceptor] = None,
                  fail_closed: bool = True,
                  step: Optional[int] = None,
                  scan_all_blocks: bool = False) -> Any:
    """Scan a message ``content`` value — a string or a list of content blocks.

    Only ``tool_result`` blocks are inspected by default; pass
    ``scan_all_blocks=True`` to also scan ``text``/``thinking`` blocks (model
    output), which is a policy choice rather than signal handling.

    A bare-string ``content`` is scanned as-is: some integrations flatten tool
    output into a plain string, and that path must not be a bypass.
    """
    if content is None:
        return content
    if isinstance(content, (str, bytes)):
        _guard_text(content, interceptor=interceptor, fail_closed=fail_closed,
                    step=step, source="tool_result")
        return content
    blocks = content if isinstance(content, (list, tuple)) else [content]
    for block in blocks:
        if scan_all_blocks or is_tool_result_block(block):
            guard_tool_result(block, interceptor=interceptor,
                              fail_closed=fail_closed, step=step)
        elif _block_type(block) is None:
            # Unclassifiable block: scan it rather than skip it. Fail closed.
            _guard_text(block, interceptor=interceptor, fail_closed=fail_closed,
                        step=step, source="tool_result")
    return content


def guard_messages(messages: Any, *,
                   interceptor: Optional[HaltInterceptor] = None,
                   fail_closed: bool = True,
                   step: Optional[int] = None,
                   scan_all_blocks: bool = False) -> Any:
    """Scan a Messages API ``messages`` array before the next model turn.

    Every message's ``content`` is walked for ``tool_result`` blocks regardless
    of the message ``role`` — the API delivers tool results under the ``user``
    role, so filtering on role would miss them entirely.

    Returns:
        ``messages``, unmodified, when no stop signal is present.
    """
    if messages is None:
        return messages
    items = messages if isinstance(messages, (list, tuple)) else [messages]
    for message in items:
        content = _block_field(message, "content")
        if content is None:
            # Not a recognizable message: scan the whole object. Fail closed.
            _guard_text(message, interceptor=interceptor,
                        fail_closed=fail_closed, step=step, source="tool_result")
            continue
        guard_content(content, interceptor=interceptor, fail_closed=fail_closed,
                      step=step, scan_all_blocks=scan_all_blocks)
    return messages


def guard_response(response: Any, *,
                   interceptor: Optional[HaltInterceptor] = None,
                   fail_closed: bool = True,
                   step: Optional[int] = None) -> Any:
    """Scan a ``Message`` response object's content blocks (model output).

    Off the default enforcement path — model text is echoable — but useful when
    a subagent's transcript is folded back in as a tool result.
    """
    _guard_text(response, interceptor=interceptor, fail_closed=fail_closed,
                step=step, source="model_output")
    return response


def wrap_tool(fn: Optional[Callable] = None, *,
              interceptor: Optional[HaltInterceptor] = None,
              fail_closed: bool = True,
              source: str = "tool_result") -> Callable:
    """Decorate a tool handler so its return value is halt-scanned.

    Works bare or with arguments, on sync or async handlers, and on handlers
    returning a plain string, a dict, or a list of content blocks::

        @wrap_tool
        def run_query(sql: str) -> str:
            ...

    :func:`functools.wraps` preserves ``__name__``, ``__doc__``, ``__wrapped__``
    and annotations, so tool-schema generation that introspects the handler is
    unaffected.
    """

    def decorator(target: Callable) -> Callable:
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = await target(*args, **kwargs)
                _guard_text(result, interceptor=interceptor,
                            fail_closed=fail_closed, source=source)
                return result

            return async_wrapper

        @functools.wraps(target)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = target(*args, **kwargs)
            _guard_text(result, interceptor=interceptor,
                        fail_closed=fail_closed, source=source)
            return result

        return wrapper

    if fn is None:
        return decorator
    return decorator(fn)


def build_halt_tool_result(tool_use_id: str, sentinel: str) -> dict:
    """Build a ``tool_result`` block carrying ``sentinel`` — for tests and servers.

    Handy for exercising an agent harness end-to-end (and for a governed server
    that answers a tool call with a halt) without hand-writing the block shape.
    """
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "is_error": True,
        "content": [{"type": "text", "text": sentinel}],
    }
