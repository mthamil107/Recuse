"""Tests for recuse.policy: the directive -> behavior mapping and its execution.

No network, no API key, and NO REAL SLEEPING: every throttle test injects a fake
``sleep`` that records what it was asked to wait for, so we can assert the delay
cap is honored without the suite ever blocking.
"""
from __future__ import annotations

import logging

import pytest

from recuse.policy import (
    DEFAULT_MAX_DELAY,
    DEFAULT_THROTTLE_DELAY,
    MAX_DELAY_CEILING,
    Action,
    Decision,
    Policy,
    PolicyStop,
    default_policy,
)
from recuse.signal import Signal, build_signal, parse_signal

DENY = "RECUSE/0.1 deny; reason=production; scope=all-automation; id=deny-1"
HALT = "RECUSE/0.2 halt; reason=operator-request; id=halt-1"
THROTTLE = "RECUSE/0.1 throttle; reason=load; id=thr-1"
WARN = "RECUSE/0.1 warn; reason=production; id=warn-1"
UNKNOWN = "RECUSE/0.2 frobnicate; reason=other"
MALFORMED = "garbage RECUSE/ oops not-a-sentinel"


class FakeSleep:
    """Records every requested delay instead of blocking."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)

    @property
    def total(self):
        return sum(self.calls)


def sig(text):
    s = parse_signal(text)
    assert s is not None, text
    return s


# --------------------------------------------------------------------------- mapping
@pytest.mark.parametrize("text,expected", [
    (DENY, Action.STOP),
    (HALT, Action.STOP),
    (THROTTLE, Action.THROTTLE),
    (WARN, Action.WARN),
])
def test_each_directive_maps_to_its_normative_action(text, expected):
    assert Policy().decide(sig(text)).action == expected


def test_unknown_directive_failcloses_to_stop():
    d = Policy().decide(sig(UNKNOWN))
    assert d.action == Action.STOP
    assert d.is_stop is True
    assert "fail-closed" in d.reason


def test_malformed_sentinel_failcloses_to_stop():
    d = Policy().decide(sig(MALFORMED))
    assert d.action == Action.STOP
    assert d.event["malformed"] is True


def test_fail_open_degrades_unknown_to_warn_not_stop():
    d = Policy(fail_closed=False).decide(sig(UNKNOWN))
    assert d.action == Action.WARN
    assert d.should_proceed is True


def test_no_signal_proceeds():
    d = Policy().decide(None)
    assert d.action == Action.PROCEED
    assert d.directive is None and d.signal is None
    assert d.delay_seconds == 0.0


def test_decide_text_parses_then_decides():
    p = Policy()
    assert p.decide_text("df: 80% used\n\n" + HALT).action == Action.STOP
    assert p.decide_text("all systems nominal").action == Action.PROCEED


def test_action_for_maps_tokens_directly():
    p = Policy()
    assert p.action_for("deny") == Action.STOP
    assert p.action_for("warn") == Action.WARN
    assert p.action_for(None) == Action.STOP


def test_action_is_a_plain_string_token():
    assert Action.STOP == "stop"
    assert Action.THROTTLE.value == "throttle"
    assert {a.value for a in Action} == {"stop", "throttle", "warn", "proceed"}


# --------------------------------------------------------------------------- config
def test_policy_is_configurable_per_directive():
    p = Policy(on_deny=Action.WARN, on_throttle=Action.STOP)
    assert p.decide(sig(DENY)).action == Action.WARN
    assert p.decide(sig(THROTTLE)).action == Action.STOP
    # untouched arms keep their normative default
    assert p.decide(sig(HALT)).action == Action.STOP


def test_policy_accepts_string_actions():
    p = Policy(on_warn="stop")
    assert p.decide(sig(WARN)).action == Action.STOP


def test_policy_rejects_a_bogus_action():
    with pytest.raises(ValueError):
        Policy(on_deny="explode")


def test_default_policy_helper_matches_defaults():
    p = default_policy()
    assert p.max_delay == DEFAULT_MAX_DELAY
    assert p.decide(sig(DENY)).action == Action.STOP


# --------------------------------------------------------------------------- throttle
def test_throttle_delay_defaults_when_no_hint():
    d = Policy().decide(sig(THROTTLE))
    assert d.delay_seconds == DEFAULT_THROTTLE_DELAY


def test_throttle_honors_a_delay_hint_below_the_cap():
    d = Policy().decide(sig("RECUSE/0.1 throttle; reason=load; delay=3.5"))
    assert d.delay_seconds == 3.5


def test_throttle_accepts_retry_after_as_the_hint():
    d = Policy().decide(sig("RECUSE/0.1 throttle; reason=load; retry-after=4"))
    assert d.delay_seconds == 4.0


def test_throttle_delay_is_hard_capped_at_the_default_10s():
    d = Policy().decide(sig("RECUSE/0.1 throttle; delay=99999"))
    assert d.delay_seconds == DEFAULT_MAX_DELAY == 10.0


def test_throttle_delay_is_capped_by_a_lower_configured_cap():
    fake = FakeSleep()
    p = Policy(max_delay=1.5)
    d = p.apply(sig("RECUSE/0.1 throttle; delay=600"), sleep=fake)
    assert d.action == Action.THROTTLE
    assert fake.calls == [1.5]
    assert fake.total <= p.max_delay


def test_configured_cap_cannot_exceed_the_absolute_ceiling():
    p = Policy(max_delay=10 ** 9)
    assert p.max_delay == MAX_DELAY_CEILING
    fake = FakeSleep()
    p.apply(sig("RECUSE/0.1 throttle; delay=10000000"), sleep=fake)
    assert fake.calls[0] <= MAX_DELAY_CEILING


def test_unbounded_or_negative_cap_is_rejected():
    with pytest.raises(ValueError):
        Policy(max_delay=float("inf"))
    with pytest.raises(ValueError):
        Policy(max_delay=float("nan"))
    with pytest.raises(ValueError):
        Policy(max_delay=-1)
    with pytest.raises(ValueError):
        Policy(max_delay="soon")


def test_garbage_and_negative_delay_hints_fall_back_to_the_default():
    p = Policy()
    assert p.decide(sig("RECUSE/0.1 throttle; delay=soon")).delay_seconds == \
        DEFAULT_THROTTLE_DELAY
    assert p.decide(sig("RECUSE/0.1 throttle; delay=-5")).delay_seconds == \
        DEFAULT_THROTTLE_DELAY
    assert p.decide(sig("RECUSE/0.1 throttle; delay=inf")).delay_seconds == \
        DEFAULT_THROTTLE_DELAY


def test_delay_is_zero_for_every_non_throttle_action():
    p = Policy()
    for text in (DENY, HALT, WARN, UNKNOWN):
        assert p.decide(sig(text)).delay_seconds == 0.0


def test_apply_clamps_even_a_hand_mutated_decision():
    """The cap is re-applied at the point of use, not only at decide()."""
    p = Policy(max_delay=2.0)
    fake = FakeSleep()
    d = p.decide(sig(THROTTLE))
    d.delay_seconds = 9999.0  # simulate tampering / a hand-built Decision
    p.apply(d, sleep=fake)
    assert fake.calls == [2.0]


def test_throttle_is_delay_only_and_never_raises():
    fake = FakeSleep()
    d = Policy().apply(sig(THROTTLE), sleep=fake)
    assert d.action == Action.THROTTLE
    assert d.should_proceed is True
    assert fake.calls == [DEFAULT_THROTTLE_DELAY]


def test_zero_cap_means_no_sleep_at_all():
    fake = FakeSleep()
    Policy(max_delay=0).apply(sig(THROTTLE), sleep=fake)
    assert fake.calls == []


# --------------------------------------------------------------------------- apply
def test_apply_raises_policystop_on_deny():
    fake = FakeSleep()
    with pytest.raises(PolicyStop) as exc:
        Policy().apply(sig(DENY), sleep=fake)
    assert exc.value.decision.action == Action.STOP
    assert exc.value.signal.directive == "deny"
    assert exc.value.decision.id == "deny-1"
    assert fake.calls == []  # a stop never sleeps


def test_apply_raises_policystop_on_halt():
    with pytest.raises(PolicyStop):
        Policy().apply(sig(HALT))


def test_warn_does_not_stop_and_does_not_sleep():
    fake = FakeSleep()
    d = Policy().apply(sig(WARN), sleep=fake)
    assert d.action == Action.WARN
    assert d.is_stop is False and d.should_proceed is True
    assert fake.calls == []


def test_apply_with_no_signal_proceeds():
    d = Policy().apply(None)
    assert d.action == Action.PROCEED


def test_apply_text_end_to_end():
    fake = FakeSleep()
    d = Policy().apply_text("output...\n" + THROTTLE, sleep=fake)
    assert d.action == Action.THROTTLE and fake.calls == [DEFAULT_THROTTLE_DELAY]
    with pytest.raises(PolicyStop):
        Policy().apply_text("output...\n" + DENY, sleep=fake)


def test_apply_on_a_decision_does_not_re_emit_its_event():
    p = Policy()
    d = p.decide(sig(WARN))
    assert len(p.events) == 1
    p.apply(d)
    assert len(p.events) == 1


# --------------------------------------------------------------------------- events
def test_every_decision_emits_a_structured_event():
    seen = []
    p = Policy(on_event=seen.append)
    for text in (DENY, HALT, THROTTLE, WARN, UNKNOWN):
        p.decide(sig(text))
    p.decide(None)
    assert len(seen) == 6
    assert seen == p.events
    assert [e["action"] for e in seen] == [
        "stop", "stop", "throttle", "warn", "stop", "proceed"]


def test_event_carries_the_required_fields():
    seen = []
    Policy(on_event=seen.append).decide(sig(HALT))
    e = seen[0]
    for key in ("timestamp", "directive", "action", "reason", "signal_id"):
        assert key in e, key
    assert e["directive"] == "halt"
    assert e["action"] == "stop"
    assert e["signal_id"] == "halt-1"
    assert e["signal_reason"] == "operator-request"
    assert isinstance(e["reason"], str) and e["reason"]
    assert e["timestamp"].startswith("20")


def test_throttle_event_records_the_capped_delay():
    seen = []
    Policy(max_delay=1.0, on_event=seen.append).decide(
        sig("RECUSE/0.1 throttle; delay=500"))
    assert seen[0]["delay_seconds"] == 1.0


def test_events_are_logged_through_stdlib_logging(caplog):
    with caplog.at_level(logging.DEBUG, logger="recuse.policy"):
        Policy().decide(sig(DENY))
        Policy().decide(sig(WARN))
    assert len(caplog.records) == 2
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[1].levelno == logging.WARNING
    assert "recuse.decision" in caplog.records[0].getMessage()


def test_logging_can_be_disabled_while_the_callback_still_fires(caplog):
    seen = []
    with caplog.at_level(logging.DEBUG, logger="recuse.policy"):
        Policy(log=None, on_event=seen.append).decide(sig(DENY))
    assert caplog.records == []
    assert len(seen) == 1


def test_apply_emits_exactly_one_event_per_signal():
    seen = []
    p = Policy(on_event=seen.append)
    with pytest.raises(PolicyStop):
        p.apply(sig(DENY))
    assert len(seen) == 1


# --------------------------------------------------------------------------- decision
def test_decision_to_dict_is_json_shaped():
    d = Policy().decide(sig(THROTTLE))
    out = d.to_dict()
    assert out["action"] == "throttle"
    assert out["directive"] == "throttle"
    assert out["signal"]["directive"] == "throttle"
    assert isinstance(out["delay_seconds"], float)


def test_decision_reason_is_human_readable():
    d = Policy().decide(sig(DENY))
    assert "deny" in d.reason
    assert "reason=production" in d.reason
    assert "id=deny-1" in d.reason


def test_decision_id_is_none_without_a_signal():
    assert Policy().decide(None).id is None


def test_round_trip_with_build_signal():
    p = Policy()
    for directive, expected in (("deny", Action.STOP), ("halt", Action.STOP),
                                ("throttle", Action.THROTTLE), ("warn", Action.WARN)):
        line = build_signal(directive, reason="production", id="x-1")
        assert p.decide_text(line).action == expected


def test_signal_object_built_by_hand_is_accepted():
    d = Policy().decide(Signal(directive="warn", params={"reason": "x"}))
    assert d.action == Action.WARN


def test_decision_dataclass_can_be_constructed_directly():
    d = Decision(action=Action.PROCEED, reason="manual")
    assert d.should_proceed and not d.is_stop


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
