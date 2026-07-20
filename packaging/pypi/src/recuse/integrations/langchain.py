"""LangChain / LangGraph adapter: halt enforcement via a callback handler.

Drop :class:`RecuseCallbackHandler` into any LangChain runnable's ``callbacks``
and every tool result is scanned for a RECUSE stop sentinel. The instant one
appears, :class:`recuse.HaltEnforced` is raised *from inside the callback*, which
propagates out of the agent executor and terminates the run before the model can
be handed the output or issue another tool call::

    from recuse.integrations.langchain import RecuseCallbackHandler

    handler = RecuseCallbackHandler()
    try:
        agent.invoke({"input": "..."}, config={"callbacks": [handler]})
    except HaltEnforced as stop:
        print("halted:", stop.signal.reason)

**LangChain is never imported here.** The handler is duck-typed against the
``BaseCallbackHandler`` interface (the ``on_*`` methods plus the ``ignore_*`` /
``raise_error`` / ``run_inline`` attributes LangChain probes), so the class
imports, instantiates and unit-tests with LangChain absent. LangChain accepts any
object exposing that surface. If you need a genuine subclass (some strict
integrations isinstance-check), call :func:`make_callback_handler`, which imports
the base class lazily and falls back to the plain class when it is unavailable.

``raise_error`` is set ``True`` on purpose: LangChain swallows callback
exceptions unless a handler opts in, and a swallowed halt is not enforcement.
"""
from __future__ import annotations

from typing import Any, Optional

from ..halt import HaltEnforced, HaltInterceptor
from ..signal import Signal
from . import _guard_text

__all__ = [
    "RecuseCallbackHandler",
    "RecuseAsyncCallbackHandler",
    "make_callback_handler",
    "guard_tool_messages",
]


class RecuseCallbackHandler:
    """A LangChain-compatible callback that enforces RECUSE ``halt``/``deny``.

    Args:
        interceptor: an optional shared :class:`recuse.HaltInterceptor`. When
            given it records the halt event (and fires its ``on_halt`` hook)
            before the exception propagates; otherwise the handler raises
            :class:`recuse.HaltEnforced` directly.
        fail_closed: treat a malformed ``RECUSE/`` fragment or an unknown
            directive as a stop (default, and strongly recommended).
        scan_model_output: also scan the model's own text (``on_llm_end``).
            Off by default — model text is attacker-echoable and stopping on it
            is a policy choice, whereas tool output is the signal's real channel.
        scan_retriever: also scan retrieved documents (``on_retriever_end``).
    """

    #: LangChain probes these; ``raise_error`` must be True or the halt is swallowed.
    raise_error = True
    #: Run the callback synchronously in the executing thread so the raise lands
    #: in the agent's call stack rather than a background callback thread.
    run_inline = True
    ignore_llm = False
    ignore_chain = False
    ignore_agent = False
    ignore_retriever = False
    ignore_chat_model = False
    ignore_retry = False
    ignore_custom_event = False

    def __init__(self, interceptor: Optional[HaltInterceptor] = None, *,
                 fail_closed: bool = True, scan_model_output: bool = False,
                 scan_retriever: bool = True):
        self.interceptor = interceptor
        self.fail_closed = fail_closed
        self.scan_model_output = scan_model_output
        self.scan_retriever = scan_retriever
        #: The signal that tripped this handler, once it has tripped.
        self.signal: Optional[Signal] = None
        #: Number of tool results inspected (useful in tests / telemetry).
        self.inspected = 0

    # -- core ------------------------------------------------------------------
    def guard(self, value: Any, *, source: str = "tool_result") -> None:
        """Scan one value; raise :class:`recuse.HaltEnforced` on a stop signal."""
        self.inspected += 1
        try:
            _guard_text(value, interceptor=self.interceptor,
                        fail_closed=self.fail_closed, source=source)
        except HaltEnforced as stop:
            self.signal = stop.signal
            raise

    # -- LangChain callback surface -------------------------------------------
    def on_tool_end(self, output: Any, **kwargs: Any) -> Any:
        """The enforcement point: a halt in tool output stops the run here."""
        self.guard(output, source="tool_result")
        return output

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """A halt can also arrive as a tool *error* string."""
        self.guard(error, source="tool_error")

    def on_retriever_end(self, documents: Any, **kwargs: Any) -> Any:
        if self.scan_retriever:
            self.guard(documents, source="retriever_result")
        return documents

    def on_retriever_error(self, error: BaseException, **kwargs: Any) -> None:
        if self.scan_retriever:
            self.guard(error, source="retriever_error")

    def on_llm_end(self, response: Any, **kwargs: Any) -> Any:
        if self.scan_model_output:
            self.guard(response, source="model_output")
        return response

    # -- no-op members LangChain may call -------------------------------------
    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        return None

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        return None

    def on_chat_model_start(self, serialized: Any, messages: Any,
                            **kwargs: Any) -> None:
        return None

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        return None

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        return None

    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        return None

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        return None

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        return None

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        return None

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        return None

    def on_text(self, text: Any, **kwargs: Any) -> None:
        return None

    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        return None

    def on_custom_event(self, name: str, data: Any, **kwargs: Any) -> None:
        return None

    def __repr__(self) -> str:
        return ("RecuseCallbackHandler(fail_closed={0!r}, scan_model_output={1!r}, "
                "halted={2!r})".format(self.fail_closed, self.scan_model_output,
                                       self.signal is not None))


class RecuseAsyncCallbackHandler(RecuseCallbackHandler):
    """Async twin of :class:`RecuseCallbackHandler` for ``ainvoke``/``astream``.

    LangChain dispatches to coroutine callbacks when the runnable is async, so
    the ``on_*`` hooks are redefined as coroutines. They delegate to the same
    synchronous :meth:`guard`, and the raised :class:`recuse.HaltEnforced`
    propagates through the awaiting agent loop identically.
    """

    async def on_tool_end(self, output: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.guard(output, source="tool_result")
        return output

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:  # type: ignore[override]
        self.guard(error, source="tool_error")

    async def on_retriever_end(self, documents: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if self.scan_retriever:
            self.guard(documents, source="retriever_result")
        return documents

    async def on_retriever_error(self, error: BaseException, **kwargs: Any) -> None:  # type: ignore[override]
        if self.scan_retriever:
            self.guard(error, source="retriever_error")

    async def on_llm_end(self, response: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if self.scan_model_output:
            self.guard(response, source="model_output")
        return response


def _base_callback_handler(asynchronous: bool = False) -> Optional[type]:
    """Lazily locate LangChain's callback base class; ``None`` if not installed."""
    name = "AsyncCallbackHandler" if asynchronous else "BaseCallbackHandler"
    for module_path in ("langchain_core.callbacks", "langchain_core.callbacks.base",
                        "langchain.callbacks.base"):
        try:
            module = __import__(module_path, fromlist=[name])
        except Exception:
            continue
        base = getattr(module, name, None)
        if isinstance(base, type):
            return base
    return None


def make_callback_handler(*args: Any, asynchronous: bool = False,
                          **kwargs: Any) -> RecuseCallbackHandler:
    """Build a handler that is a *real* LangChain ``BaseCallbackHandler`` subclass.

    Use this only when something in your stack isinstance-checks the handler.
    LangChain is imported lazily inside this function; if it is not installed the
    plain duck-typed :class:`RecuseCallbackHandler` is returned instead, so this
    function never raises ``ImportError``.

    Args:
        asynchronous: subclass ``AsyncCallbackHandler`` and return the async
            handler, for ``ainvoke``/``astream`` pipelines.
        *args, **kwargs: forwarded to :class:`RecuseCallbackHandler`.
    """
    mixin = RecuseAsyncCallbackHandler if asynchronous else RecuseCallbackHandler
    base = _base_callback_handler(asynchronous=asynchronous)
    if base is None:
        return mixin(*args, **kwargs)
    cls = type("Recuse{0}".format(base.__name__), (mixin, base), {})
    return cls(*args, **kwargs)


def guard_tool_messages(messages: Any, *,
                        interceptor: Optional[HaltInterceptor] = None,
                        fail_closed: bool = True) -> Any:
    """Scan a list of LangChain messages, stopping on a halt in any ``ToolMessage``.

    Useful for LangGraph nodes, where tool results land in state rather than
    passing through a callback. Messages are identified by ``type``/``role`` of
    ``tool``; anything unrecognized is scanned wholesale rather than skipped.
    """
    items = messages if isinstance(messages, (list, tuple)) else [messages]
    for message in items:
        kind = getattr(message, "type", None) or getattr(message, "role", None)
        if kind is None and isinstance(message, dict):
            kind = message.get("type") or message.get("role")
        if kind is not None and str(kind).lower() not in ("tool", "function"):
            continue
        _guard_text(message, interceptor=interceptor, fail_closed=fail_closed,
                    source="tool_result")
    return messages
