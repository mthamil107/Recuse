"""Tests for recuse.integrations: the agent-framework halt adapters.

These run with NO third-party agent framework installed and make NO network
calls. Every framework object is a local fake that mimics the real shape
(LangChain's ``ToolMessage``, the OpenAI Agents SDK's ``function_call_output``
item, an Anthropic ``tool_result`` content block), which is the point: the
adapters are duck-typed, so they must import, run and enforce with langchain /
openai / anthropic all absent.

The invariants under test, for every adapter:

* a halt in tool output raises :class:`HaltEnforced` (enforcement, not advice),
* benign output passes through unchanged,
* the ``https://github.com/mthamil107/Recuse`` policy URL does NOT false-trip,
* malformed ``RECUSE/`` fragments fail closed (stop) by default,
* the decorators preserve function metadata via :func:`functools.wraps`.
"""
from __future__ import annotations

import asyncio
import importlib
import sys

import pytest

from recuse import HaltEnforced, HaltInterceptor
from recuse.integrations import anthropic_sdk, langchain as lc_adapter, openai_agents

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
HALT_BLOCK = "rows: 3\n" + HALT_LINE + "\nThis operation has been halted."
DENY_LINE = "RECUSE/0.1 deny; reason=production"
WARN_LINE = "RECUSE/0.1 warn; reason=production"
MALFORMED = "unexpected RECUSE/ oops not-a-sentinel"
BENIGN = "OK: 3 rows returned, no anomalies."
#: The project's own policy URL. Detection is case-sensitive on ``RECUSE/``, so
#: this must never be mistaken for a sentinel.
POLICY_URL = "See the policy at https://github.com/mthamil107/Recuse for details."


# =========================================================================== fakes
class FakeToolMessage:
    """Mimics ``langchain_core.messages.ToolMessage``."""

    type = "tool"

    def __init__(self, content, tool_call_id="call_1"):
        self.content = content
        self.tool_call_id = tool_call_id


class FakeAIMessage:
    type = "ai"

    def __init__(self, content):
        self.content = content


class FakeDocument:
    """Mimics a LangChain retriever ``Document``."""

    def __init__(self, page_content, metadata=None):
        self.page_content = page_content
        self.text = page_content
        self.metadata = metadata or {}


class FakeToolResult:
    """Mimics an OpenAI Agents SDK tool result object."""

    def __init__(self, output):
        self.output = output


class FakeTextBlock:
    """Mimics an Anthropic SDK ``TextBlock`` (object, not dict)."""

    type = "text"

    def __init__(self, text):
        self.text = text


class FakeToolResultBlock:
    """Mimics an Anthropic SDK ``tool_result`` block as an object."""

    type = "tool_result"

    def __init__(self, content, tool_use_id="toolu_01", is_error=False):
        self.content = content
        self.tool_use_id = tool_use_id
        self.is_error = is_error


def anthropic_tool_result(text, is_error=False):
    """The canonical dict shape the Messages API expects for a tool result."""
    return {
        "type": "tool_result",
        "tool_use_id": "toolu_01",
        "is_error": is_error,
        "content": [{"type": "text", "text": text}],
    }


def anthropic_user_turn(text):
    return {"role": "user", "content": [anthropic_tool_result(text)]}


# =========================================================================== package
def test_no_third_party_imported_at_import_time():
    """Importing the adapters must not pull in langchain / openai / anthropic."""
    for name in ("langchain", "langchain_core", "openai", "anthropic", "agents"):
        assert name not in sys.modules, "{0} was imported at import time".format(name)


def test_submodules_are_lazy_attributes():
    import recuse.integrations as integrations

    assert integrations.langchain is lc_adapter
    assert integrations.openai_agents is openai_agents
    assert integrations.anthropic_sdk is anthropic_sdk


def test_convenience_reexports_resolve():
    import recuse.integrations as integrations

    assert integrations.RecuseCallbackHandler is lc_adapter.RecuseCallbackHandler
    assert integrations.RecuseRunHooks is openai_agents.RecuseRunHooks
    assert callable(integrations.make_callback_handler)
    assert "langchain" in dir(integrations)


def test_unknown_attribute_raises_attribute_error():
    import recuse.integrations as integrations

    with pytest.raises(AttributeError):
        integrations.does_not_exist


def test_adapters_import_without_frameworks_installed():
    """Reimport each adapter with the frameworks blocked from sys.path."""
    blocked = ("langchain", "langchain_core", "openai", "anthropic", "agents")

    class Blocker:
        def find_module(self, fullname, path=None):
            return self.find_spec(fullname, path)

        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in blocked:
                raise ImportError("blocked for test: " + fullname)
            return None

    blocker = Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        for mod in ("recuse.integrations.langchain",
                    "recuse.integrations.openai_agents",
                    "recuse.integrations.anthropic_sdk"):
            sys.modules.pop(mod, None)
            assert importlib.import_module(mod) is not None
    finally:
        sys.meta_path.remove(blocker)


# =========================================================================== langchain
def test_langchain_halt_in_tool_output_raises():
    handler = lc_adapter.RecuseCallbackHandler()
    with pytest.raises(HaltEnforced) as excinfo:
        handler.on_tool_end(HALT_BLOCK, run_id="r1")
    assert excinfo.value.signal.directive == "halt"
    assert excinfo.value.signal.id == "abc-123"
    assert handler.signal is not None


def test_langchain_halt_in_tool_message_object_raises():
    handler = lc_adapter.RecuseCallbackHandler()
    with pytest.raises(HaltEnforced):
        handler.on_tool_end(FakeToolMessage(HALT_BLOCK), run_id="r1")


def test_langchain_halt_in_nested_dict_raises():
    handler = lc_adapter.RecuseCallbackHandler()
    payload = {"status": "error", "detail": {"body": HALT_LINE}}
    with pytest.raises(HaltEnforced):
        handler.on_tool_end(payload, run_id="r1")


def test_langchain_deny_midsession_raises():
    handler = lc_adapter.RecuseCallbackHandler()
    with pytest.raises(HaltEnforced) as excinfo:
        handler.on_tool_end(DENY_LINE, run_id="r1")
    assert excinfo.value.signal.directive == "deny"


def test_langchain_benign_output_passes_through():
    handler = lc_adapter.RecuseCallbackHandler()
    assert handler.on_tool_end(BENIGN, run_id="r1") == BENIGN
    assert handler.signal is None
    assert handler.inspected == 1


def test_langchain_advisory_does_not_stop():
    handler = lc_adapter.RecuseCallbackHandler()
    assert handler.on_tool_end(WARN_LINE, run_id="r1") == WARN_LINE


def test_langchain_policy_url_does_not_false_trip():
    handler = lc_adapter.RecuseCallbackHandler()
    assert handler.on_tool_end(POLICY_URL, run_id="r1") == POLICY_URL


def test_langchain_malformed_fails_closed():
    handler = lc_adapter.RecuseCallbackHandler()
    with pytest.raises(HaltEnforced) as excinfo:
        handler.on_tool_end(MALFORMED, run_id="r1")
    assert excinfo.value.signal.malformed is True


def test_langchain_malformed_passes_when_fail_closed_disabled():
    handler = lc_adapter.RecuseCallbackHandler(fail_closed=False)
    assert handler.on_tool_end(MALFORMED, run_id="r1") == MALFORMED


def test_langchain_tool_error_is_scanned():
    handler = lc_adapter.RecuseCallbackHandler()
    with pytest.raises(HaltEnforced):
        handler.on_tool_error(RuntimeError(HALT_LINE), run_id="r1")


def test_langchain_model_output_scanned_only_when_enabled():
    off = lc_adapter.RecuseCallbackHandler()
    off.on_llm_end(FakeAIMessage(HALT_LINE))  # no raise: model output ignored

    on = lc_adapter.RecuseCallbackHandler(scan_model_output=True)
    with pytest.raises(HaltEnforced):
        on.on_llm_end(FakeAIMessage(HALT_LINE))


def test_langchain_retriever_documents_scanned():
    handler = lc_adapter.RecuseCallbackHandler()
    docs = [FakeDocument("intro"), FakeDocument(HALT_LINE)]
    with pytest.raises(HaltEnforced):
        handler.on_retriever_end(docs)

    off = lc_adapter.RecuseCallbackHandler(scan_retriever=False)
    assert off.on_retriever_end(docs) is docs


def test_langchain_handler_exposes_callback_contract():
    handler = lc_adapter.RecuseCallbackHandler()
    # LangChain swallows callback exceptions unless raise_error is set.
    assert handler.raise_error is True
    assert handler.run_inline is True
    for flag in ("ignore_llm", "ignore_chain", "ignore_agent", "ignore_retriever",
                 "ignore_chat_model", "ignore_retry", "ignore_custom_event"):
        assert getattr(handler, flag) is False
    for method in ("on_tool_start", "on_tool_end", "on_tool_error", "on_llm_start",
                   "on_llm_end", "on_chain_start", "on_chain_end", "on_agent_action",
                   "on_agent_finish", "on_text", "on_retry", "on_custom_event"):
        assert callable(getattr(handler, method))
    assert "RecuseCallbackHandler" in repr(handler)


def test_langchain_noop_callbacks_do_not_raise():
    handler = lc_adapter.RecuseCallbackHandler()
    assert handler.on_tool_start({}, HALT_LINE) is None  # inputs are not scanned
    assert handler.on_chain_end({"out": BENIGN}) is None
    assert handler.on_agent_action(object()) is None
    assert handler.on_text(BENIGN) is None


def test_langchain_async_handler_enforces():
    handler = lc_adapter.RecuseAsyncCallbackHandler()
    with pytest.raises(HaltEnforced):
        asyncio.run(handler.on_tool_end(HALT_BLOCK, run_id="r1"))
    assert asyncio.run(
        lc_adapter.RecuseAsyncCallbackHandler().on_tool_end(BENIGN)) == BENIGN


def test_langchain_make_callback_handler_works_either_way():
    """Returns a working handler whether or not LangChain is importable."""
    handler = lc_adapter.make_callback_handler()
    assert isinstance(handler, lc_adapter.RecuseCallbackHandler)
    with pytest.raises(HaltEnforced):
        handler.on_tool_end(HALT_LINE)

    async_handler = lc_adapter.make_callback_handler(asynchronous=True)
    assert isinstance(async_handler, lc_adapter.RecuseAsyncCallbackHandler)


def test_langchain_make_callback_handler_uses_base_class_when_available(monkeypatch):
    """When LangChain *is* installed, the returned handler subclasses its base."""

    class PretendBase:
        pass

    monkeypatch.setattr(lc_adapter, "_base_callback_handler",
                        lambda asynchronous=False: PretendBase)
    handler = lc_adapter.make_callback_handler()
    assert isinstance(handler, PretendBase)
    assert isinstance(handler, lc_adapter.RecuseCallbackHandler)
    with pytest.raises(HaltEnforced):
        handler.on_tool_end(HALT_LINE)


def test_langchain_guard_tool_messages_scans_only_tool_messages():
    messages = [FakeAIMessage("thinking"), FakeToolMessage(HALT_BLOCK)]
    with pytest.raises(HaltEnforced):
        lc_adapter.guard_tool_messages(messages)

    # A halt echoed by the *model* is not a tool result and does not stop.
    assert lc_adapter.guard_tool_messages([FakeAIMessage(HALT_LINE)]) is not None
    benign = [FakeToolMessage(BENIGN), FakeAIMessage(POLICY_URL)]
    assert lc_adapter.guard_tool_messages(benign) is benign


def test_langchain_guard_tool_messages_accepts_dict_messages():
    with pytest.raises(HaltEnforced):
        lc_adapter.guard_tool_messages([{"role": "tool", "content": HALT_LINE}])


def test_langchain_interceptor_records_the_event():
    interceptor = HaltInterceptor()
    handler = lc_adapter.RecuseCallbackHandler(interceptor)
    with pytest.raises(HaltEnforced):
        handler.on_tool_end(HALT_BLOCK)
    assert interceptor.halted is True
    assert interceptor.signal.directive == "halt"
    assert interceptor.events and interceptor.events[0]["event"] == "halt_detected"
    # Anything after a trip is refused outright.
    with pytest.raises(HaltEnforced):
        handler.interceptor.inspect(BENIGN)


# =========================================================================== openai
def test_openai_halt_in_tool_output_raises():
    with pytest.raises(HaltEnforced) as excinfo:
        openai_agents.guard_tool_output(HALT_BLOCK)
    assert excinfo.value.signal.directive == "halt"
    assert excinfo.value.signal.reason == "operator-request"


def test_openai_benign_output_returned_unchanged():
    payload = {"rows": 3, "note": BENIGN}
    assert openai_agents.guard_tool_output(payload) is payload
    assert openai_agents.guard_tool_output(BENIGN) == BENIGN


def test_openai_policy_url_does_not_false_trip():
    assert openai_agents.guard_tool_output(POLICY_URL) == POLICY_URL
    assert openai_agents.guard_tool_output({"docs": POLICY_URL})["docs"] == POLICY_URL


def test_openai_advisory_does_not_stop():
    assert openai_agents.guard_tool_output(WARN_LINE) == WARN_LINE


def test_openai_malformed_fails_closed():
    with pytest.raises(HaltEnforced) as excinfo:
        openai_agents.guard_tool_output(MALFORMED)
    assert excinfo.value.signal.malformed is True
    assert openai_agents.guard_tool_output(MALFORMED, fail_closed=False) == MALFORMED


def test_openai_halt_in_bytes_and_nested_structures():
    with pytest.raises(HaltEnforced):
        openai_agents.guard_tool_output(HALT_LINE.encode("utf-8"))
    with pytest.raises(HaltEnforced):
        openai_agents.guard_tool_output({"a": {"b": [BENIGN, HALT_LINE]}})
    with pytest.raises(HaltEnforced):
        openai_agents.guard_tool_output(FakeToolResult(HALT_LINE))


def test_openai_guard_messages_scans_tool_role():
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": HALT_BLOCK},
    ]
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages(messages)


def test_openai_guard_messages_ignores_non_tool_roles_by_default():
    messages = [{"role": "user", "content": HALT_LINE},
                {"role": "assistant", "content": HALT_LINE}]
    assert openai_agents.guard_messages(messages) is messages
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages(messages, scan_all_roles=True)


def test_openai_guard_messages_scans_responses_api_items():
    items = [
        {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": HALT_LINE},
    ]
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages(items)


def test_openai_guard_messages_benign_and_policy_url():
    messages = [{"role": "tool", "content": BENIGN},
                {"role": "tool", "content": POLICY_URL}]
    assert openai_agents.guard_messages(messages) is messages


def test_openai_guard_messages_malformed_shapes_fail_closed():
    # An entry with neither role nor type is unclassifiable -> scanned anyway.
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages([{"payload": HALT_LINE}])
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages([HALT_LINE])
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages([{"role": "tool", "content": MALFORMED}])
    # A bare non-list argument is still scanned.
    with pytest.raises(HaltEnforced):
        openai_agents.guard_messages({"role": "tool", "content": HALT_LINE})
    assert openai_agents.guard_messages(None) is None
    assert openai_agents.guard_messages([]) == []


def test_openai_guard_response():
    with pytest.raises(HaltEnforced):
        openai_agents.guard_response({"output_text": HALT_LINE})
    assert openai_agents.guard_response(BENIGN) == BENIGN


def test_openai_wrap_tool_bare_decorator():
    @openai_agents.wrap_tool
    def read_file(path):
        """Read a file."""
        return HALT_BLOCK if path == "/etc/governed" else BENIGN

    assert read_file("/tmp/ok") == BENIGN
    with pytest.raises(HaltEnforced):
        read_file("/etc/governed")


def test_openai_wrap_tool_with_arguments_and_interceptor():
    interceptor = HaltInterceptor()

    @openai_agents.wrap_tool(interceptor=interceptor)
    def fetch(url):
        return HALT_LINE

    with pytest.raises(HaltEnforced):
        fetch("https://example.com")
    assert interceptor.halted is True


def test_openai_wrap_tool_preserves_metadata():
    def http_get(url: str, timeout: int = 5) -> str:
        """Fetch a URL and return the body."""
        return BENIGN

    wrapped = openai_agents.wrap_tool(http_get)
    assert wrapped.__name__ == "http_get"
    assert wrapped.__doc__ == "Fetch a URL and return the body."
    assert wrapped.__wrapped__ is http_get
    assert wrapped.__module__ == http_get.__module__
    assert wrapped.__annotations__ == http_get.__annotations__

    import inspect as _inspect
    assert _inspect.signature(wrapped) == _inspect.signature(http_get)


def test_openai_wrap_tool_async_preserves_metadata_and_enforces():
    @openai_agents.wrap_tool
    async def a_fetch(url: str) -> str:
        """Async fetch."""
        return HALT_LINE if "governed" in url else BENIGN

    import inspect as _inspect
    assert _inspect.iscoroutinefunction(a_fetch)
    assert a_fetch.__name__ == "a_fetch"
    assert a_fetch.__doc__ == "Async fetch."
    assert asyncio.run(a_fetch("https://ok")) == BENIGN
    with pytest.raises(HaltEnforced):
        asyncio.run(a_fetch("https://governed"))


def test_openai_run_hooks_enforce_on_tool_end():
    hooks = openai_agents.RecuseRunHooks()
    with pytest.raises(HaltEnforced):
        hooks.run_sync(hooks.on_tool_end(None, None, None, HALT_BLOCK))
    assert hooks.inspected == 1

    ok = openai_agents.RecuseRunHooks()
    ok.run_sync(ok.on_tool_end(None, None, None, FakeToolResult(BENIGN)))
    assert ok.inspected == 1


def test_openai_run_hooks_model_output_gated():
    hooks = openai_agents.RecuseRunHooks()
    hooks.run_sync(hooks.on_llm_end(response=HALT_LINE))  # ignored by default
    hooks.run_sync(hooks.on_agent_end(output=HALT_LINE))
    hooks.run_sync(hooks.on_tool_start())
    hooks.run_sync(hooks.on_agent_start())
    hooks.run_sync(hooks.on_handoff())

    strict = openai_agents.RecuseRunHooks(scan_model_output=True)
    with pytest.raises(HaltEnforced):
        strict.run_sync(strict.on_llm_end(response=HALT_LINE))
    with pytest.raises(HaltEnforced):
        strict.run_sync(strict.on_agent_end(output=HALT_LINE))
    assert "RecuseRunHooks" in repr(strict)


def test_openai_loop_stops_before_the_next_tool_call():
    """End-to-end: the halt must prevent every subsequent action."""
    executed = []

    @openai_agents.wrap_tool
    def tool(name):
        executed.append(name)
        return HALT_BLOCK if name == "second" else BENIGN

    with pytest.raises(HaltEnforced):
        for name in ("first", "second", "third"):
            tool(name)
    assert executed == ["first", "second"]  # "third" never ran


# =========================================================================== anthropic
def test_anthropic_halt_in_tool_result_block_raises():
    with pytest.raises(HaltEnforced) as excinfo:
        anthropic_sdk.guard_tool_result(anthropic_tool_result(HALT_BLOCK))
    assert excinfo.value.signal.directive == "halt"
    assert excinfo.value.signal.id == "abc-123"


def test_anthropic_benign_tool_result_returned_unchanged():
    block = anthropic_tool_result(BENIGN)
    assert anthropic_sdk.guard_tool_result(block) is block


def test_anthropic_policy_url_does_not_false_trip():
    block = anthropic_tool_result(POLICY_URL)
    assert anthropic_sdk.guard_tool_result(block) is block
    messages = [anthropic_user_turn(POLICY_URL)]
    assert anthropic_sdk.guard_messages(messages) is messages


def test_anthropic_advisory_does_not_stop():
    block = anthropic_tool_result(WARN_LINE)
    assert anthropic_sdk.guard_tool_result(block) is block


def test_anthropic_malformed_fails_closed():
    block = anthropic_tool_result(MALFORMED)
    with pytest.raises(HaltEnforced) as excinfo:
        anthropic_sdk.guard_tool_result(block)
    assert excinfo.value.signal.malformed is True
    assert anthropic_sdk.guard_tool_result(block, fail_closed=False) is block


def test_anthropic_string_content_tool_result():
    """``content`` may be a bare string; that path must not be a bypass."""
    block = {"type": "tool_result", "tool_use_id": "t1", "content": HALT_LINE}
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_tool_result(block)


def test_anthropic_mixed_content_blocks():
    block = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": [
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
            {"type": "text", "text": HALT_LINE},
        ],
    }
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_tool_result(block)


def test_anthropic_sdk_object_blocks():
    block = FakeToolResultBlock([FakeTextBlock(HALT_BLOCK)])
    assert anthropic_sdk.is_tool_result_block(block) is True
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_tool_result(block)
    assert anthropic_sdk.guard_tool_result(
        FakeToolResultBlock([FakeTextBlock(BENIGN)])) is not None


def test_anthropic_is_tool_result_block():
    assert anthropic_sdk.is_tool_result_block(anthropic_tool_result(BENIGN)) is True
    assert anthropic_sdk.is_tool_result_block({"type": "text", "text": "hi"}) is False
    assert anthropic_sdk.is_tool_result_block({}) is False
    assert anthropic_sdk.is_tool_result_block("plain string") is False


def test_anthropic_guard_messages_finds_tool_result_under_user_role():
    """Tool results ride the *user* role — filtering on role would miss them."""
    messages = [
        {"role": "user", "content": "list the tables"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "sql", "input": {}}]},
        anthropic_user_turn(HALT_BLOCK),
    ]
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages(messages)


def test_anthropic_guard_messages_ignores_model_text_by_default():
    messages = [{"role": "assistant", "content": [{"type": "text",
                                                   "text": HALT_LINE}]}]
    assert anthropic_sdk.guard_messages(messages) is messages
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages(messages, scan_all_blocks=True)


def test_anthropic_guard_messages_malformed_shapes_fail_closed():
    # A message with no ``content`` at all is unclassifiable -> scanned whole.
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages([{"role": "user", "note": HALT_LINE}])
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages([HALT_LINE])
    # An unclassifiable content block (no ``type``) is scanned too.
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages([{"role": "user",
                                       "content": [{"blob": HALT_LINE}]}])
    assert anthropic_sdk.guard_messages(None) is None
    assert anthropic_sdk.guard_messages([]) == []


def test_anthropic_guard_content_string_and_list():
    assert anthropic_sdk.guard_content(BENIGN) == BENIGN
    assert anthropic_sdk.guard_content(None) is None
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_content(HALT_LINE)
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_content([anthropic_tool_result(HALT_LINE)])


def test_anthropic_guard_response():
    class FakeMessage:
        role = "assistant"
        content = [FakeTextBlock(HALT_LINE)]

    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_response(FakeMessage())
    assert anthropic_sdk.guard_response(BENIGN) == BENIGN


def test_anthropic_wrap_tool_enforces_and_preserves_metadata():
    @anthropic_sdk.wrap_tool
    def run_query(sql: str) -> str:
        """Run a SQL query."""
        return HALT_BLOCK if "governed" in sql else BENIGN

    assert run_query.__name__ == "run_query"
    assert run_query.__doc__ == "Run a SQL query."
    assert run_query.__wrapped__ is not None
    assert run_query.__annotations__ == run_query.__wrapped__.__annotations__
    assert run_query("select 1") == BENIGN
    with pytest.raises(HaltEnforced):
        run_query("select * from governed")


def test_anthropic_wrap_tool_returning_content_blocks():
    interceptor = HaltInterceptor()

    @anthropic_sdk.wrap_tool(interceptor=interceptor)
    def tool():
        return [{"type": "text", "text": HALT_LINE}]

    with pytest.raises(HaltEnforced):
        tool()
    assert interceptor.halted is True
    assert interceptor.events[0]["directive"] == "halt"


def test_anthropic_wrap_tool_async():
    @anthropic_sdk.wrap_tool
    async def a_tool(flag):
        """Async tool."""
        return HALT_LINE if flag else BENIGN

    import inspect as _inspect
    assert _inspect.iscoroutinefunction(a_tool)
    assert a_tool.__doc__ == "Async tool."
    assert asyncio.run(a_tool(False)) == BENIGN
    with pytest.raises(HaltEnforced):
        asyncio.run(a_tool(True))


def test_anthropic_build_halt_tool_result_round_trips():
    block = anthropic_sdk.build_halt_tool_result("toolu_42", HALT_LINE)
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "toolu_42"
    assert block["is_error"] is True
    with pytest.raises(HaltEnforced):
        anthropic_sdk.guard_messages([{"role": "user", "content": [block]}])


# =========================================================================== shared
def test_halt_enforced_carries_context_for_every_adapter():
    for call in (
        lambda: lc_adapter.RecuseCallbackHandler().on_tool_end(HALT_BLOCK),
        lambda: openai_agents.guard_tool_output(HALT_BLOCK),
        lambda: anthropic_sdk.guard_tool_result(anthropic_tool_result(HALT_BLOCK)),
    ):
        with pytest.raises(HaltEnforced) as excinfo:
            call()
        stop = excinfo.value
        assert stop.signal.directive == "halt"
        assert stop.signal.reason == "operator-request"
        assert stop.source == "tool_result"
        assert "RECUSE halt enforced" in str(stop)


def test_lowercase_and_substring_anchors_do_not_trip():
    """Detection is case-sensitive; near-misses must stay benign."""
    for text in ("recuse/0.2 halt", "PRERECUSE", "Recuse/0.2 halt",
                 "the RECUSED transaction", POLICY_URL):
        assert openai_agents.guard_tool_output(text) == text
        assert anthropic_sdk.guard_tool_result(anthropic_tool_result(text)) is not None
        assert lc_adapter.RecuseCallbackHandler().on_tool_end(text) == text
