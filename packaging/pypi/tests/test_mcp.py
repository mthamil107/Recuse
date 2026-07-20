"""Tests for recuse.mcp: RECUSE halt enforcement on MCP tool calls.

These run with NO network, NO API key, and — importantly — with the ``mcp``
package NOT installed: every shape used here is a hand-built stand-in for what a
real MCP client returns, which is the point of the module being duck-typed.
"""
from __future__ import annotations

import asyncio

import pytest

from recuse import HaltEnforced, HaltInterceptor
from recuse.mcp import (
    RecuseMCPMiddleware,
    detect_stop_in_result,
    extract_text,
    guard_tool_result,
    install,
    wrap_async_call_tool,
    wrap_call_tool,
)

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
BENIGN = "3 files matched: a.py, b.py, c.py"
# The project's own policy URL must never be mistaken for a sentinel.
POLICY_URL = "See the policy at https://github.com/mthamil107/Recuse for details."


def test_module_never_imports_mcp_at_module_scope():
    """recuse.mcp must add no runtime dependency: no module-level ``mcp`` import."""
    import recuse.mcp as m

    assert "mcp" not in vars(m), "the mcp package must only be imported lazily"
    assert m.MCP_SOURCE == "mcp_tool_result"

    source = open(m.__file__, "r", encoding="utf-8").read()
    for line in source.splitlines():
        if line.startswith("import mcp") or line.startswith("from mcp"):
            raise AssertionError("module-level mcp import: " + line)


# --------------------------------------------------------------------------- shapes
class TextBlock:
    """A pydantic-ish ``TextContent`` stand-in."""

    def __init__(self, text):
        self.type = "text"
        self.text = text


class CallToolResult:
    """A pydantic-ish ``CallToolResult`` stand-in."""

    def __init__(self, blocks, is_error=False):
        self.content = list(blocks)
        self.isError = is_error


HALT_SHAPES = {
    "plain_string": HALT_LINE,
    "bytes": HALT_LINE.encode("utf-8"),
    "content_blocks_dict": {"content": [{"type": "text", "text": HALT_LINE}]},
    "content_blocks_dict_multi": {"content": [
        {"type": "text", "text": BENIGN},
        {"type": "text", "text": "disk ok\n" + HALT_LINE},
    ]},
    "error_result_dict": {"content": [{"type": "text", "text": HALT_LINE}],
                          "isError": True},
    "bare_block_list": [{"type": "text", "text": HALT_LINE}],
    "structured_content": {"structuredContent": {"status": "stopped",
                                                 "notice": HALT_LINE}},
    "object_with_content": CallToolResult([TextBlock(HALT_LINE)]),
    "object_error": CallToolResult([TextBlock(HALT_LINE)], is_error=True),
    "nested_vendor_field": {"meta": {"headers": {"x-notice": HALT_LINE}}},
    "text_key_only": {"text": HALT_LINE},
}

BENIGN_SHAPES = {
    "plain_string": BENIGN,
    "bytes": BENIGN.encode("utf-8"),
    "content_blocks_dict": {"content": [{"type": "text", "text": BENIGN}]},
    "bare_block_list": [{"type": "text", "text": BENIGN}],
    "object_with_content": CallToolResult([TextBlock(BENIGN)]),
    "policy_url_dict": {"content": [{"type": "text", "text": POLICY_URL}]},
    "policy_url_string": POLICY_URL,
    "advisory_warn": {"content": [{"type": "text",
                                   "text": "RECUSE/0.1 warn; reason=production"}]},
    "advisory_throttle": "RECUSE/0.1 throttle; reason=load",
    "empty_dict": {},
    "none": None,
    "empty_list": [],
    "number": 42,
}


@pytest.mark.parametrize("name", sorted(HALT_SHAPES))
def test_extract_text_finds_the_sentinel_in_every_shape(name):
    assert "RECUSE/0.2 halt" in extract_text(HALT_SHAPES[name])


@pytest.mark.parametrize("name", sorted(HALT_SHAPES))
def test_guard_raises_on_every_halt_shape(name):
    with pytest.raises(HaltEnforced) as exc:
        guard_tool_result(HALT_SHAPES[name])
    assert exc.value.signal.directive == "halt"
    assert exc.value.signal.reason == "operator-request"


@pytest.mark.parametrize("name", sorted(BENIGN_SHAPES))
def test_guard_passes_benign_results_through(name):
    result = BENIGN_SHAPES[name]
    assert guard_tool_result(result) is result


def test_policy_url_does_not_false_trip():
    assert detect_stop_in_result(POLICY_URL) is None
    assert detect_stop_in_result({"content": [{"type": "text", "text": POLICY_URL}]}) is None
    assert guard_tool_result(POLICY_URL) == POLICY_URL


def test_detect_stop_in_result_returns_signal_without_raising():
    sig = detect_stop_in_result({"content": [{"type": "text", "text": HALT_LINE}]})
    assert sig is not None and sig.id == "abc-123"
    assert detect_stop_in_result(BENIGN) is None


def test_deny_directive_also_stops_midsession():
    with pytest.raises(HaltEnforced):
        guard_tool_result({"content": [{"type": "text",
                                        "text": "RECUSE/0.1 deny; reason=prod"}]})


def test_malformed_sentinel_failcloses():
    payload = {"content": [{"type": "text", "text": "junk RECUSE/ not-a-sentinel"}]}
    with pytest.raises(HaltEnforced) as exc:
        guard_tool_result(payload)
    assert exc.value.signal.malformed is True


def test_malformed_ignored_when_fail_closed_disabled():
    payload = {"content": [{"type": "text", "text": "junk RECUSE/ not-a-sentinel"}]}
    assert guard_tool_result(payload, fail_closed=False) is payload


def test_unknown_directive_failcloses():
    with pytest.raises(HaltEnforced) as exc:
        guard_tool_result({"content": [{"type": "text",
                                        "text": "RECUSE/0.2 frobnicate; reason=x"}]})
    assert exc.value.signal.malformed is True


def test_cyclic_result_does_not_hang():
    payload = {"content": [{"type": "text", "text": BENIGN}]}
    payload["self"] = payload
    assert guard_tool_result(payload) is payload


def test_guard_records_tool_name_in_source():
    with pytest.raises(HaltEnforced) as exc:
        guard_tool_result(HALT_LINE, tool_name="read_file")
    assert exc.value.source == "mcp_tool_result:read_file"


def test_guard_shares_interceptor_state():
    ic = HaltInterceptor()
    guard_tool_result(BENIGN, ic)
    assert ic.halted is False
    with pytest.raises(HaltEnforced):
        guard_tool_result(HALT_LINE, ic, pending=2)
    assert ic.halted is True
    assert ic.events and ic.events[0]["event"] == "halt_detected"
    assert ic.actions_prevented == 2


# --------------------------------------------------------------------------- sync middleware
def make_server(halt_at=None):
    """A fake MCP server that keeps answering forever, delivering a halt at a
    chosen call index so we can prove the *middleware* stops the loop."""
    calls = []

    def call_tool(name, args=None):
        calls.append((name, args))
        n = len(calls)
        if halt_at is not None and n == halt_at:
            return {"content": [{"type": "text", "text": "listing...\n" + HALT_LINE}]}
        return {"content": [{"type": "text", "text": "result {0}".format(n)}]}

    return call_tool, calls


def test_middleware_passes_benign_results_through():
    call_tool, calls = make_server()
    guarded = RecuseMCPMiddleware(call_tool)
    out = guarded("search", {"q": "x"})
    assert out["content"][0]["text"] == "result 1"
    assert calls == [("search", {"q": "x"})]
    assert guarded.halted is False
    assert guarded.calls_made == 1


def test_middleware_raises_and_stops_further_tool_calls():
    call_tool, calls = make_server(halt_at=3)
    guarded = RecuseMCPMiddleware(call_tool)

    guarded("a", {})
    guarded("b", {})
    with pytest.raises(HaltEnforced) as exc:
        guarded("c", {})
    assert exc.value.signal.directive == "halt"
    assert len(calls) == 3

    # An agent that "keeps going" gets nothing: the tool is never invoked again.
    for tool in ("d", "e"):
        with pytest.raises(HaltEnforced):
            guarded(tool, {})
    assert len(calls) == 3, "no tool ran after the halt"
    assert guarded.halted is True
    assert guarded.calls_prevented == 2


def test_middleware_signal_is_exposed_after_trip():
    call_tool, _ = make_server(halt_at=1)
    guarded = RecuseMCPMiddleware(call_tool)
    with pytest.raises(HaltEnforced):
        guarded("x", {})
    assert guarded.signal.id == "abc-123"
    assert guarded.signal.reason == "operator-request"


def test_middleware_shares_one_interceptor_across_servers():
    """A halt from server A must stop calls to server B in the same session."""
    ic = HaltInterceptor()
    a_calls, b_calls = [], []

    def server_a(name, args=None):
        a_calls.append(name)
        return {"content": [{"type": "text", "text": HALT_LINE}]}

    def server_b(name, args=None):
        b_calls.append(name)
        return "fine"

    guarded_a = RecuseMCPMiddleware(server_a, ic)
    guarded_b = RecuseMCPMiddleware(server_b, ic)

    with pytest.raises(HaltEnforced):
        guarded_a("stop_me", {})
    with pytest.raises(HaltEnforced):
        guarded_b("other_server_tool", {})
    assert b_calls == [], "the other server's tool must not run"


def test_middleware_can_scan_outgoing_args():
    call_tool, calls = make_server()
    guarded = RecuseMCPMiddleware(call_tool, scan_args=True)
    with pytest.raises(HaltEnforced) as exc:
        guarded("write", {"body": HALT_LINE})
    assert exc.value.source == "mcp_tool_args:write"
    assert calls == [], "the tool must not run when its own args carry a halt"


def test_middleware_does_not_scan_args_by_default():
    call_tool, calls = make_server()
    guarded = RecuseMCPMiddleware(call_tool)
    guarded("write", {"body": HALT_LINE})
    assert len(calls) == 1


def test_middleware_without_call_tool_raises_runtimeerror():
    guarded = RecuseMCPMiddleware()
    with pytest.raises(RuntimeError):
        guarded("x", {})


def test_wrap_call_tool_helper():
    call_tool, calls = make_server(halt_at=2)
    guarded = wrap_call_tool(call_tool)
    assert guarded("a")["content"][0]["text"] == "result 1"
    with pytest.raises(HaltEnforced):
        guarded("b")
    assert guarded.recuse_middleware.halted is True
    with pytest.raises(HaltEnforced):
        guarded("c")
    assert len(calls) == 2


def test_middleware_guard_on_an_already_obtained_result():
    guarded = RecuseMCPMiddleware()
    assert guarded.guard(BENIGN, tool_name="t") == BENIGN
    with pytest.raises(HaltEnforced):
        guarded.guard(HALT_LINE, tool_name="t")


# --------------------------------------------------------------------------- async
def make_async_server(halt_at=None):
    calls = []

    async def call_tool(name, args=None):
        calls.append(name)
        n = len(calls)
        if halt_at is not None and n == halt_at:
            return CallToolResult([TextBlock("partial output\n" + HALT_LINE)])
        return CallToolResult([TextBlock("result {0}".format(n))])

    return call_tool, calls


def test_async_middleware_passes_benign_through():
    call_tool, calls = make_async_server()

    async def scenario():
        guarded = RecuseMCPMiddleware(call_tool)
        return await guarded.acall("search", {"q": "x"})

    out = asyncio.run(scenario())
    assert out.content[0].text == "result 1"
    assert calls == ["search"]


def test_async_middleware_halts_and_prevents_further_calls():
    call_tool, calls = make_async_server(halt_at=2)
    seen = {}

    async def scenario():
        guarded = RecuseMCPMiddleware(call_tool)
        await guarded.acall("a", {})
        try:
            await guarded.acall("b", {})
        except HaltEnforced as exc:
            seen["first"] = exc
        try:
            await guarded.acall("c", {})
        except HaltEnforced as exc:
            seen["second"] = exc
        return guarded

    guarded = asyncio.run(scenario())
    assert seen["first"].signal.directive == "halt"
    assert "second" in seen
    assert calls == ["a", "b"], "no tool ran after the halt"
    assert guarded.halted is True


def test_wrap_async_call_tool_helper():
    call_tool, calls = make_async_server(halt_at=1)

    async def scenario():
        guarded = wrap_async_call_tool(call_tool)
        with pytest.raises(HaltEnforced):
            await guarded("a", {})
        return guarded

    guarded = asyncio.run(scenario())
    assert guarded.recuse_middleware.halted is True
    assert calls == ["a"]


def test_wrap_async_helper_is_a_coroutine_function():
    import inspect

    call_tool, _ = make_async_server()
    assert inspect.iscoroutinefunction(wrap_async_call_tool(call_tool))


# --------------------------------------------------------------------------- install()
class FakeSession:
    """A stand-in for ``mcp.ClientSession`` with an instance-level call_tool."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.call_tool = self._call_tool

    def _call_tool(self, name, args=None):
        self.calls.append(name)
        return self._results.pop(0)


def test_install_wraps_a_sync_session():
    session = FakeSession(["ok", {"content": [{"type": "text", "text": HALT_LINE}]}])
    middleware = install(session)
    assert session.call_tool("a") == "ok"
    with pytest.raises(HaltEnforced):
        session.call_tool("b")
    assert middleware.halted is True
    with pytest.raises(HaltEnforced):
        session.call_tool("c")
    assert session.calls == ["a", "b"]


class FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

        async def call_tool(name, args=None):
            self.calls.append(name)
            return self._results.pop(0)

        self.call_tool = call_tool


def test_install_wraps_an_async_session():
    session = FakeAsyncSession(["ok", HALT_LINE])

    async def scenario():
        install(session)
        assert await session.call_tool("a") == "ok"
        with pytest.raises(HaltEnforced):
            await session.call_tool("b")

    asyncio.run(scenario())
    assert session.calls == ["a", "b"]


class CustomClient:
    def __init__(self):
        self.calls = []
        self.invoke_tool = self._invoke

    def _invoke(self, name, args=None):
        self.calls.append(name)
        return HALT_LINE


def test_install_finds_alternate_attribute_names():
    client = CustomClient()
    install(client)
    with pytest.raises(HaltEnforced):
        client.invoke_tool("x")


def test_install_honors_explicit_attr():
    class Odd:
        def __init__(self):
            self.do_the_thing = lambda name, args=None: HALT_LINE

    odd = Odd()
    install(odd, attr="do_the_thing")
    with pytest.raises(HaltEnforced):
        odd.do_the_thing("x")


def test_install_raises_typeerror_when_nothing_wrappable():
    class Empty:
        pass

    with pytest.raises(TypeError) as exc:
        install(Empty())
    assert "no wrappable tool-call attribute" in str(exc.value)


def test_install_raises_typeerror_on_readonly_attribute():
    class Frozen:
        __slots__ = ()

        def call_tool(self, name, args=None):
            return "ok"

    with pytest.raises(TypeError) as exc:
        install(Frozen())
    assert "could not be replaced" in str(exc.value)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
