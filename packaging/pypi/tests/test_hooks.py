"""Tests for recuse.hooks: the Claude Code PreToolUse/PostToolUse hook.

Pure stdlib. ``handle_hook_event`` is exercised directly as a pure function, and
``main`` is driven end-to-end by monkeypatching stdin/stdout so the stdin->stdout
->exit-code contract is checked exactly as Claude Code would use it.
"""
from __future__ import annotations

import io
import json

import pytest

from recuse.hooks import (
    EXIT_ALLOW,
    EXIT_BLOCK,
    allow_decision,
    block_decision,
    handle_hook_event,
    is_block,
    main,
    scan_event,
)

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
POLICY_URL = "See https://github.com/mthamil107/Recuse for the policy."


def pre_event(**kwargs):
    event = {
        "session_id": "abc123",
        "transcript_path": "/home/u/.claude/projects/x/transcript.jsonl",
        "cwd": "/home/u/proj",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://example.com/api"},
    }
    event.update(kwargs)
    return event


def post_event(**kwargs):
    event = pre_event(hook_event_name="PostToolUse")
    event["tool_output"] = "HTTP/1.1 200 OK\nall good"
    event.update(kwargs)
    return event


def run_main(payload, monkeypatch, argv=None, raw=None):
    """Drive main() over stdin/stdout; return (exit_code, decision, stderr)."""
    text = raw if raw is not None else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = main(argv or [])
    body = out.getvalue().strip()
    return code, (json.loads(body) if body else None), err.getvalue()


# --------------------------------------------------------------------------- allow
def test_benign_pretooluse_is_allowed():
    decision = handle_hook_event(pre_event())
    assert is_block(decision) is False
    assert decision["continue"] is True
    # Never force-allow: the hook may restrict, never bypass user permissions.
    assert "permissionDecision" not in decision.get("hookSpecificOutput", {})


def test_benign_posttooluse_is_allowed():
    assert is_block(handle_hook_event(post_event())) is False


def test_policy_url_does_not_false_trip():
    assert is_block(handle_hook_event(post_event(tool_output=POLICY_URL))) is False
    assert is_block(handle_hook_event(
        pre_event(tool_input={"url": "https://github.com/mthamil107/Recuse"}))) is False
    assert scan_event(post_event(tool_output=POLICY_URL)) is None


def test_advisory_directives_do_not_block():
    for text in ("RECUSE/0.1 warn; reason=production",
                 "RECUSE/0.1 throttle; reason=load"):
        assert is_block(handle_hook_event(post_event(tool_output=text))) is False


def test_session_metadata_is_not_scanned():
    """A path or transcript that happens to contain the token must not trip."""
    event = pre_event(transcript_path="/tmp/RECUSE/0.2 halt; reason=x/t.jsonl",
                      cwd="/RECUSE/0.2 halt")
    assert is_block(handle_hook_event(event)) is False


# --------------------------------------------------------------------------- block
def test_halt_in_tool_output_blocks():
    decision = handle_hook_event(post_event(
        tool_output="fetched 3 rows\n" + HALT_LINE))
    assert is_block(decision) is True
    assert decision["decision"] == "block"
    assert decision["continue"] is False
    assert "operator-request" in decision["reason"]
    assert "abc-123" in decision["reason"]
    assert decision["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_halt_in_tool_input_blocks_pretooluse_with_deny():
    decision = handle_hook_event(pre_event(
        tool_input={"command": "echo '" + HALT_LINE + "'"}))
    assert is_block(decision) is True
    specific = decision["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert "operator-request" in specific["permissionDecisionReason"]
    assert "Bash" in decision["reason"]


def test_halt_nested_deep_in_tool_output_object_blocks():
    decision = handle_hook_event(post_event(
        tool_output={"body": {"headers": {"x-notice": HALT_LINE}}}))
    assert is_block(decision) is True


def test_tool_response_and_tool_result_aliases_are_scanned():
    for key in ("tool_response", "tool_result", "toolOutput", "stderr"):
        event = post_event()
        event.pop("tool_output")
        event[key] = HALT_LINE
        assert is_block(handle_hook_event(event)) is True, key


def test_deny_directive_blocks():
    decision = handle_hook_event(post_event(
        tool_output="RECUSE/0.1 deny; reason=production"))
    assert is_block(decision) is True
    assert "deny" in decision["reason"]


def test_block_reason_tells_the_agent_not_to_route_around_it():
    reason = handle_hook_event(post_event(tool_output=HALT_LINE))["reason"]
    assert "Do not retry" in reason
    assert "route around" in reason


# --------------------------------------------------------------------------- fail-closed
def test_malformed_sentinel_failcloses_to_block():
    decision = handle_hook_event(post_event(tool_output="junk RECUSE/ not-a-line"))
    assert is_block(decision) is True
    assert "malformed" in decision["reason"]


def test_unknown_directive_failcloses_to_block():
    assert is_block(handle_hook_event(
        post_event(tool_output="RECUSE/0.2 frobnicate; reason=x"))) is True


@pytest.mark.parametrize("payload", [None, "a string", 42, ["a", "list"], object()])
def test_non_object_payload_failcloses_to_block(payload):
    decision = handle_hook_event(payload)
    assert is_block(decision) is True
    assert "malformed payload" in decision["reason"]


def test_non_object_payload_allowed_when_fail_open():
    assert is_block(handle_hook_event(None, fail_closed=False)) is False


def test_malformed_sentinel_allowed_when_fail_open():
    decision = handle_hook_event(post_event(tool_output="junk RECUSE/ oops"),
                                 fail_closed=False)
    assert is_block(decision) is False


def test_empty_event_is_allowed():
    assert is_block(handle_hook_event({})) is False


def test_missing_hook_event_name_defaults_to_pretooluse():
    decision = handle_hook_event({"tool_output": HALT_LINE})
    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_is_block_fails_closed_on_garbage():
    assert is_block("not a decision") is True
    assert is_block(None) is True


def test_decision_dicts_are_json_serializable():
    json.dumps(block_decision("because", hook_event_name="PreToolUse"))
    json.dumps(allow_decision(hook_event_name="PostToolUse"))


# --------------------------------------------------------------------------- main()
def test_main_allows_benign_event(monkeypatch):
    code, decision, err = run_main(post_event(), monkeypatch)
    assert code == EXIT_ALLOW == 0
    assert decision["continue"] is True
    assert err == ""


def test_main_blocks_on_halt_and_exits_two(monkeypatch):
    code, decision, err = run_main(post_event(tool_output=HALT_LINE), monkeypatch)
    assert code == EXIT_BLOCK == 2
    assert decision["decision"] == "block"
    # stdout JSON is ignored by Claude Code on exit 2, so the reason is on stderr too.
    assert "operator-request" in err


def test_main_blocks_on_unparseable_stdin(monkeypatch):
    code, decision, err = run_main(None, monkeypatch, raw="{not json at all")
    assert code == EXIT_BLOCK
    assert is_block(decision) is True
    assert err.strip() != ""


def test_main_blocks_when_a_sentinel_rides_non_json_stdin(monkeypatch):
    code, decision, _ = run_main(None, monkeypatch, raw="oops " + HALT_LINE)
    assert code == EXIT_BLOCK
    assert "operator-request" in decision["reason"]


def test_main_allows_empty_stdin(monkeypatch):
    code, decision, _ = run_main(None, monkeypatch, raw="")
    assert code == EXIT_ALLOW
    assert is_block(decision) is False


def test_main_fail_open_flag_allows_malformed_stdin(monkeypatch):
    code, decision, _ = run_main(None, monkeypatch, raw="{nope",
                                 argv=["--fail-open"])
    assert code == EXIT_ALLOW
    assert is_block(decision) is False


def test_main_exit_zero_flag_still_emits_the_block_decision(monkeypatch):
    code, decision, err = run_main(post_event(tool_output=HALT_LINE), monkeypatch,
                                   argv=["--exit-zero"])
    assert code == 0
    assert decision["decision"] == "block"
    assert "operator-request" in err


def test_main_reads_from_a_file(tmp_path, monkeypatch):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(post_event(tool_output=HALT_LINE)), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = main(["--input", str(path)])
    assert code == EXIT_BLOCK
    assert json.loads(out.getvalue())["decision"] == "block"


def test_main_blocks_when_input_file_is_missing(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = main(["--input", "no/such/file-does-not-exist.json"])
    assert code == EXIT_BLOCK
    assert is_block(json.loads(out.getvalue())) is True


def test_main_output_is_exactly_one_json_line(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(post_event())))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    main([])
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    json.loads(lines[0])


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
