"""Drop-in RECUSE ``halt`` enforcement for popular agent frameworks.

Each adapter in this subpackage wires :mod:`recuse.halt` into a specific agent
stack's tool-execution path so that a ``RECUSE/0.2 halt`` (or ``deny``) sentinel
arriving in *tool output* terminates the run immediately, by raising
:class:`recuse.HaltEnforced`. The agent never gets to decide whether to comply;
the harness stops it (see :mod:`recuse.halt` for why cooperation is not enough).

Available adapters:

* :mod:`recuse.integrations.langchain` — a duck-typed LangChain callback handler.
* :mod:`recuse.integrations.openai_agents` — OpenAI Agents SDK / plain tool loops.
* :mod:`recuse.integrations.anthropic_sdk` — Anthropic Messages API / Claude Agent SDK.

**No third-party package is imported at import time.** Every adapter is written
against the *shape* of its framework (duck typing), so ``recuse.integrations``
imports and its adapters are fully testable with none of those libraries
installed. Installing this package therefore adds ZERO required dependencies.

Usage::

    from recuse.integrations.langchain import RecuseCallbackHandler
    agent.invoke(..., config={"callbacks": [RecuseCallbackHandler()]})

Submodules are resolved lazily via :pep:`562`, so ``import recuse.integrations``
does not pull in any adapter you do not touch.
"""
from __future__ import annotations

import importlib
from typing import Any, List, Optional

from ..halt import HaltEnforced, HaltInterceptor, detect_stop
from ..signal import Signal, _coerce_text

__all__ = [
    # submodules
    "langchain",
    "openai_agents",
    "anthropic_sdk",
    # unambiguous adapter names re-exported for convenience
    "RecuseCallbackHandler",
    "RecuseAsyncCallbackHandler",
    "make_callback_handler",
    "RecuseRunHooks",
]

# --------------------------------------------------------------------------- shared
# Attributes commonly carrying tool text on framework result objects
# (LangChain ``ToolMessage.content``, Agents SDK ``FunctionToolResult.output``,
# Anthropic ``TextBlock.text``, ...). Unwrapped best-effort so a sentinel buried
# in a wrapper object is still seen.
_TEXT_ATTRS = ("content", "text", "output", "result", "message", "data")

_MAX_DEPTH = 6


def _safe_coerce(value: Any) -> str:
    """:func:`recuse.signal._coerce_text` that can never raise."""
    try:
        return _coerce_text(value)
    except Exception:  # pragma: no cover - pathological __str__/__repr__
        try:
            return repr(type(value))
        except Exception:
            return ""


def _to_text(value: Any, _depth: int = 0) -> str:
    """Render an arbitrary framework object to scannable text, fail-closed.

    Strings/bytes pass through. Mappings and sequences are walked *and* serialized
    whole, and plain objects are probed for the common text attributes *and*
    serialized. The redundancy is deliberate: it is far worse to miss a halt
    sentinel than to scan the same characters twice.

    Leaf strings are emitted BEFORE the whole-object JSON dump, because JSON
    escapes real newlines to a literal ``\\n`` and would otherwise run a
    sentinel together with the rest of the tool output, corrupting the parsed
    parameters of the signal that is reported.
    """
    if value is None:
        return ""
    if isinstance(value, (str, bytes)):
        return _safe_coerce(value)
    if _depth >= _MAX_DEPTH:
        return _safe_coerce(value)

    parts: List[str] = []
    if isinstance(value, dict):
        for item in value.values():
            parts.append(_to_text(item, _depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            parts.append(_to_text(item, _depth + 1))
    else:
        for attr in _TEXT_ATTRS:
            try:
                inner = getattr(value, attr)
            except Exception:
                continue
            if inner is None or callable(inner):
                continue
            parts.append(_to_text(inner, _depth + 1))
    # Whole-object serialization last: the safety net for fields we did not walk.
    parts.append(_safe_coerce(value))
    return "\n".join(p for p in parts if p)


def _enforce(signal: Signal, *, interceptor: Optional[HaltInterceptor] = None,
             step: Optional[int] = None, source: str = "tool_result",
             pending: int = 0) -> None:
    """Raise :class:`recuse.HaltEnforced` for ``signal``, tripping ``interceptor``.

    When an interceptor is supplied it records the event (and fires ``on_halt``)
    before raising, so an application-level guard shares state with the adapter.
    """
    if interceptor is not None:
        interceptor.trip(signal, step=step, source=source, pending=pending)
    raise HaltEnforced(signal, step=0 if step is None else step, source=source,
                       actions_prevented=pending)


def _guard_text(text: Any, *, interceptor: Optional[HaltInterceptor] = None,
                fail_closed: bool = True, step: Optional[int] = None,
                source: str = "tool_result", pending: int = 0) -> Optional[Signal]:
    """Scan ``text`` and raise :class:`recuse.HaltEnforced` on a stop signal.

    Returns ``None`` when nothing stop-worthy is present (the caller proceeds).
    Never returns a signal — a detected stop always raises.
    """
    signal = detect_stop(_to_text(text), fail_closed=fail_closed)
    if signal is None:
        return None
    _enforce(signal, interceptor=interceptor, step=step, source=source,
             pending=pending)
    return signal  # pragma: no cover - _enforce always raises


# --------------------------------------------------------------------------- lazy load
_SUBMODULES = ("langchain", "openai_agents", "anthropic_sdk")

# Convenience re-exports -> (submodule, attribute)
_LAZY_ATTRS = {
    "RecuseCallbackHandler": ("langchain", "RecuseCallbackHandler"),
    "RecuseAsyncCallbackHandler": ("langchain", "RecuseAsyncCallbackHandler"),
    "make_callback_handler": ("langchain", "make_callback_handler"),
    "RecuseRunHooks": ("openai_agents", "RecuseRunHooks"),
}


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access: import an adapter only when referenced."""
    if name in _SUBMODULES:
        return importlib.import_module("{0}.{1}".format(__name__, name))
    target = _LAZY_ATTRS.get(name)
    if target is not None:
        module = importlib.import_module("{0}.{1}".format(__name__, target[0]))
        return getattr(module, target[1])
    raise AttributeError("module {0!r} has no attribute {1!r}".format(__name__, name))


def __dir__() -> List[str]:
    return sorted(set(list(globals()) + list(__all__)))
