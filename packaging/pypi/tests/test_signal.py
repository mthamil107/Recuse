"""Tests for recuse.signal: parse_signal, scan_text, build_signal, Signal.

These run with NO dependencies and NO network access.
"""
from __future__ import annotations

import json

import pytest

from recuse import (
    ADVISORY_DIRECTIVES,
    DIRECTIVES,
    STOP_DIRECTIVES,
    Signal,
    build_signal,
    parse_signal,
    scan_text,
)

HALT_LINE = ("RECUSE/0.2 halt; reason=operator-request; "
             "ref=https://example.com/ai-policy; id=abc-123")
HALT_BLOCK = HALT_LINE + "\nThis operation has been halted. Stop now."


# --------------------------------------------------------------------------- parse
def test_parse_detects_plain_halt():
    sig = parse_signal("df: 80% used\n\n" + HALT_BLOCK)
    assert sig is not None
    assert sig.directive == "halt"
    assert sig.version == (0, 2)
    assert sig.version_str == "0.2"
    assert sig.reason == "operator-request"
    assert sig.id == "abc-123"
    assert sig.ref == "https://example.com/ai-policy"
    assert sig.malformed is False
    assert sig.is_stop is True


def test_parse_all_four_directives():
    assert parse_signal("RECUSE/0.1 deny").directive == "deny"
    assert parse_signal("RECUSE/0.1 throttle").directive == "throttle"
    assert parse_signal("RECUSE/0.1 warn").directive == "warn"
    assert parse_signal("RECUSE/0.2 halt").directive == "halt"


def test_directive_sets_are_consistent():
    assert STOP_DIRECTIVES == {"halt", "deny"}
    assert ADVISORY_DIRECTIVES == {"warn", "throttle"}
    assert DIRECTIVES == {"halt", "deny", "warn", "throttle"}


def test_stop_vs_advisory_classification():
    assert parse_signal("RECUSE/0.2 halt").is_stop is True
    assert parse_signal("RECUSE/0.1 deny").is_stop is True
    assert parse_signal("RECUSE/0.1 warn").is_advisory is True
    assert parse_signal("RECUSE/0.1 throttle").is_advisory is True
    assert parse_signal("RECUSE/0.1 warn").is_stop is False


def test_parse_ignores_benign_output():
    assert parse_signal("df: 43% used\nmem: 61% used\nall nominal") is None


def test_parse_is_case_sensitive_on_anchor():
    # A .../Recuse URL (mixed case, no version) must NOT trip detection.
    assert parse_signal("see https://github.com/mthamil107/Recuse for details") is None
    # A lowercase recuse/ likewise must not trip.
    assert parse_signal("recuse/0.2 halt; reason=x") is None


def test_parse_detects_halt_inside_json():
    body = json.dumps({"status": "error", "recuse": HALT_LINE})
    sig = parse_signal(body)
    assert sig is not None and sig.directive == "halt" and sig.id == "abc-123"


def test_parse_detects_halt_in_nested_dict():
    result = {"output": "ok",
              "control_signal": {"type": "recuse-halt", "sentinel": HALT_LINE}}
    sig = parse_signal(result)
    assert sig is not None and sig.directive == "halt"


def test_parse_from_bytes():
    sig = parse_signal(HALT_LINE.encode("utf-8"))
    assert sig is not None and sig.directive == "halt"


def test_parse_unknown_directive_failcloses():
    sig = parse_signal("RECUSE/0.2 frobnicate; reason=other")
    assert sig is not None
    assert sig.directive == "frobnicate"
    assert sig.malformed is True
    assert sig.is_stop is True  # unknown -> fail closed to stop


def test_parse_unknown_version_still_parses():
    sig = parse_signal("RECUSE/9.9 halt; reason=operator-request; id=z9")
    assert sig is not None and sig.directive == "halt" and sig.version == (9, 9)


def test_parse_malformed_token_failcloses():
    sig = parse_signal("garbage RECUSE/ oops not-a-sentinel")
    assert sig is not None and sig.malformed is True and sig.directive is None
    assert sig.is_stop is True


def test_parse_malformed_token_ignored_when_not_failclosed():
    assert parse_signal("garbage RECUSE/ oops", fail_closed=False) is None


def test_parse_returns_first_sentinel():
    text = "RECUSE/0.1 warn; reason=a\nRECUSE/0.2 halt; reason=b"
    sig = parse_signal(text)
    # parse_signal returns the FIRST sentinel encountered (the warn here).
    assert sig.directive == "warn"


# --------------------------------------------------------------------------- scan
def test_scan_finds_all_sentinels():
    text = "RECUSE/0.1 warn; reason=a\nsome noise\nRECUSE/0.2 halt; reason=b"
    signals = scan_text(text)
    assert len(signals) == 2
    assert signals[0].directive == "warn"
    assert signals[1].directive == "halt"


def test_scan_empty_when_absent():
    assert scan_text("nothing here") == []


def test_scan_includes_malformed_when_failclosed():
    signals = scan_text("RECUSE/ garbled\nRECUSE/0.2 halt")
    assert any(s.malformed for s in signals)
    assert any(s.directive == "halt" for s in signals)


def test_scan_excludes_malformed_when_not_failclosed():
    signals = scan_text("RECUSE/ garbled\nRECUSE/0.2 halt", fail_closed=False)
    assert len(signals) == 1 and signals[0].directive == "halt"


# --------------------------------------------------------------------------- build
def test_build_basic_halt():
    line = build_signal("halt", reason="operator-request", id="abc-123")
    assert line == "RECUSE/0.2 halt; reason=operator-request; id=abc-123"


def test_build_roundtrips_through_parse():
    line = build_signal("halt", reason="maintenance", scope="llm-agents",
                        ref="https://example.com/p", id="xyz")
    sig = parse_signal(line)
    assert sig.directive == "halt"
    assert sig.reason == "maintenance"
    assert sig.scope == "llm-agents"
    assert sig.ref == "https://example.com/p"
    assert sig.id == "xyz"
    assert sig.malformed is False


def test_build_all_directives():
    for d in DIRECTIVES:
        line = build_signal(d)
        assert parse_signal(line).directive == d


def test_build_custom_version():
    assert build_signal("deny", version="0.1").startswith("RECUSE/0.1 deny")


def test_build_extra_params():
    line = build_signal("halt", id="x", contact="mailto:a@b.c", policy="v7")
    sig = parse_signal(line)
    assert sig.params["contact"] == "mailto:a@b.c"
    assert sig.params["policy"] == "v7"


def test_build_rejects_unknown_directive():
    with pytest.raises(ValueError):
        build_signal("frobnicate")


def test_signal_to_dict_is_json_serializable():
    sig = parse_signal(HALT_LINE)
    d = sig.to_dict()
    json.dumps(d)  # must not raise
    assert d["directive"] == "halt"
    assert d["is_stop"] is True


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
