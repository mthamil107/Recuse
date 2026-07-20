"""Act on a RECUSE signal — the directive-acting layer.

:mod:`recuse.signal` *classifies* a sentinel; :mod:`recuse.halt` enforces the one
directive that must terminate a running loop. This module closes the gap: it maps
**every** directive to a concrete behavior and performs it.

The normative mapping (spec v0.1 §4.2 / §6, v0.2 §2)::

    deny      -> STOP      MUST cease the governed operation.
    halt      -> STOP      MUST stop now, mid-session.
    throttle  -> THROTTLE  SHOULD reduce rate and proceed cautiously.
    warn      -> WARN      MAY proceed; SHOULD surface the notice.
    unknown / malformed -> STOP  (fail closed; v0.1 §4.2, §6.4)

Throttle is **delay-only and hard-capped**, mirroring the safety rule of the SSH
adapter (``adapters/ssh/recuse-pam-hook.sh``): the throttle may only ever *sleep*
for a bounded time, it never denies, and the effective delay is capped regardless
of what the signal or the configuration asks for. A signal from a remote resource
must never be able to stall a caller indefinitely.

Every decision produces a structured event dict, emitted through the stdlib
:mod:`logging` module and an optional ``on_event`` callback, so an operator can
wire the decisions into telemetry without patching anything.

Stdlib only. No enforcement here is a security control (v0.1 §9).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from .signal import DIRECTIVES, Signal, parse_signal

__all__ = [
    "Action",
    "Decision",
    "Policy",
    "PolicyStop",
    "default_policy",
    "DEFAULT_MAX_DELAY",
    "DEFAULT_THROTTLE_DELAY",
    "MAX_DELAY_CEILING",
    "DELAY_PARAMS",
    "logger",
]

# Hard ceiling on any delay this module may perform, in seconds. Matches the
# ``MAX_DELAY_CAP=10`` guarantee in the SSH PAM hook: the default cap is 10s and
# an operator may lower it, but no configuration and no signal parameter can ever
# push an effective delay above MAX_DELAY_CEILING. The delay is never unbounded.
DEFAULT_MAX_DELAY = 10.0
MAX_DELAY_CEILING = 60.0

# Applied when a ``throttle`` carries no usable delay hint. Mirrors the adapter's
# RECUSE_THROTTLE_DELAY_SECONDS default of 2.
DEFAULT_THROTTLE_DELAY = 2.0

# Signal parameters consulted, in order, for a throttle delay hint. These are
# optional: an emitter that sends none simply gets DEFAULT_THROTTLE_DELAY.
DELAY_PARAMS = ("delay", "retry-after", "retry_after")

logger = logging.getLogger("recuse.policy")


class Action(str, Enum):
    """What a caller should actually *do* about a signal.

    Subclasses :class:`str` so an action compares and serializes as its plain
    lowercase token (``Action.STOP == "stop"``) in events and JSON.
    """

    STOP = "stop"
    THROTTLE = "throttle"
    WARN = "warn"
    PROCEED = "proceed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class PolicyStop(Exception):
    """Raised by :meth:`Policy.apply` when the decided action is ``STOP``.

    Propagating this exception *is* the enforcement: control leaves the caller's
    operation before anything further can run. Carries the :class:`Decision` (and
    therefore the originating :class:`~recuse.signal.Signal`) for the operator.
    """

    def __init__(self, decision: "Decision"):
        self.decision = decision
        self.signal = decision.signal
        super().__init__(decision.reason)


@dataclass
class Decision:
    """The outcome of applying a :class:`Policy` to one signal.

    Attributes:
        action: the :class:`Action` to take.
        signal: the signal the decision was made about (``None`` when no signal
            was present at all).
        reason: a human-readable explanation, safe to show an operator.
        delay_seconds: seconds to sleep. Non-zero only for ``THROTTLE``, and
            always already clamped to the policy's cap.
        directive: the directive token the decision keyed on, or ``None``.
        event: the structured event dict emitted for this decision.
    """

    action: Action
    signal: Optional[Signal] = None
    reason: str = ""
    delay_seconds: float = 0.0
    directive: Optional[str] = None
    event: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_stop(self) -> bool:
        return self.action == Action.STOP

    @property
    def should_proceed(self) -> bool:
        """True when the caller may continue (everything except ``STOP``)."""
        return self.action != Action.STOP

    @property
    def id(self) -> Optional[str]:
        return self.signal.id if self.signal is not None else None

    def to_dict(self) -> dict:
        """A JSON-serializable view of the decision."""
        return {
            "action": self.action.value,
            "directive": self.directive,
            "reason": self.reason,
            "delay_seconds": self.delay_seconds,
            "signal": self.signal.to_dict() if self.signal is not None else None,
        }


def _coerce_action(value: Union[str, Action], label: str) -> Action:
    """Accept an :class:`Action` or its string token; reject anything else."""
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).strip().lower())
    except ValueError:
        raise ValueError(
            f"{label}={value!r} is not a valid action; expected one of "
            f"{[a.value for a in Action]}"
        )


def _parse_delay(raw: Any) -> Optional[float]:
    """Parse a delay hint into a finite, non-negative float, else ``None``."""
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0:
        return None
    return val


@dataclass
class Policy:
    """A configurable directive -> behavior mapping, with the action performed.

    The defaults are the spec's normative behavior; every arm is overridable so a
    deployment can be *stricter* (e.g. ``on_throttle=Action.STOP``) or, in a lab,
    looser. What is **not** overridable is the delay ceiling: throttle can only
    ever sleep, and only for a bounded time (see :data:`MAX_DELAY_CEILING`).

    Args:
        on_deny, on_halt, on_throttle, on_warn: the action for each directive.
        on_unknown: the action for an unknown or malformed signal. Only consulted
            when ``fail_closed`` is True; otherwise such a signal degrades to
            ``WARN`` (surfaced, not stopped).
        max_delay: hard cap in seconds on any throttle sleep. Clamped into
            ``[0, MAX_DELAY_CEILING]``. Must be finite and non-negative.
        default_delay: delay used when a throttle carries no usable hint.
        fail_closed: treat unknown/malformed signals as ``on_unknown`` (default
            ``STOP``). Mirrors :func:`recuse.signal.parse_signal`.
        on_event: optional callback invoked with every structured event dict.
        log: logger used for events. Defaults to the ``recuse.policy`` logger.
            Pass ``None`` explicitly to disable logging (the callback still runs).
    """

    on_deny: Union[str, Action] = Action.STOP
    on_halt: Union[str, Action] = Action.STOP
    on_throttle: Union[str, Action] = Action.THROTTLE
    on_warn: Union[str, Action] = Action.WARN
    on_unknown: Union[str, Action] = Action.STOP
    max_delay: float = DEFAULT_MAX_DELAY
    default_delay: float = DEFAULT_THROTTLE_DELAY
    fail_closed: bool = True
    on_event: Optional[Callable[[dict], None]] = None
    log: Optional[logging.Logger] = logger
    # Every event this policy has emitted, newest last. Handy in tests and for a
    # post-run audit without wiring a handler.
    events: List[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.on_deny = _coerce_action(self.on_deny, "on_deny")
        self.on_halt = _coerce_action(self.on_halt, "on_halt")
        self.on_throttle = _coerce_action(self.on_throttle, "on_throttle")
        self.on_warn = _coerce_action(self.on_warn, "on_warn")
        self.on_unknown = _coerce_action(self.on_unknown, "on_unknown")

        max_delay = _parse_delay(self.max_delay)
        if max_delay is None:
            raise ValueError(
                f"max_delay={self.max_delay!r} must be a finite, non-negative "
                "number of seconds; an unbounded throttle is never permitted")
        # The ceiling is absolute: configuration cannot raise it.
        self.max_delay = min(max_delay, MAX_DELAY_CEILING)

        default_delay = _parse_delay(self.default_delay)
        if default_delay is None:
            raise ValueError(
                f"default_delay={self.default_delay!r} must be a finite, "
                "non-negative number of seconds")
        self.default_delay = min(default_delay, self.max_delay)

    # -- mapping -----------------------------------------------------------------
    def action_for(self, directive: Optional[str]) -> Action:
        """The configured :class:`Action` for a directive token.

        An unrecognized (or ``None``) directive maps to ``on_unknown`` when the
        policy is fail-closed, else to ``WARN``.
        """
        if directive == "deny":
            return self.on_deny
        if directive == "halt":
            return self.on_halt
        if directive == "throttle":
            return self.on_throttle
        if directive == "warn":
            return self.on_warn
        return self.on_unknown if self.fail_closed else Action.WARN

    def delay_for(self, signal: Optional[Signal]) -> float:
        """The clamped throttle delay a signal asks for, in seconds.

        Reads the optional ``delay`` / ``retry-after`` parameter; falls back to
        ``default_delay``. The result is ALWAYS in ``[0, max_delay]`` — a remote
        emitter cannot stall the caller for longer than the local cap, whatever
        it sends.
        """
        hinted: Optional[float] = None
        if signal is not None:
            for key in DELAY_PARAMS:
                if key in signal.params:
                    hinted = _parse_delay(signal.params[key])
                    if hinted is not None:
                        break
        delay = self.default_delay if hinted is None else hinted
        return max(0.0, min(delay, self.max_delay))

    # -- decision ----------------------------------------------------------------
    def decide(self, signal: Optional[Signal]) -> Decision:
        """Map one signal to a :class:`Decision` and emit its event.

        ``signal=None`` (no RECUSE sentinel was present) decides ``PROCEED``:
        absence of a signal is not a governed condition. This method never
        sleeps and never raises on the decision itself — see :meth:`apply`.
        """
        if signal is None:
            return self._finish(Decision(
                action=Action.PROCEED,
                signal=None,
                directive=None,
                reason="no RECUSE signal present; proceeding",
            ))

        directive = signal.directive
        known = directive in DIRECTIVES and not signal.malformed
        action = self.action_for(directive if known else None)

        delay = self.delay_for(signal) if action == Action.THROTTLE else 0.0
        return self._finish(Decision(
            action=action,
            signal=signal,
            directive=directive,
            delay_seconds=delay,
            reason=self._explain(signal, action, delay, known),
        ))

    def decide_text(self, text: Any) -> Decision:
        """Parse ``text`` for a sentinel and :meth:`decide` about it.

        Convenience for callers holding raw tool output rather than a parsed
        signal. Parsing is delegated to :func:`recuse.signal.parse_signal` with
        this policy's ``fail_closed`` setting.
        """
        return self.decide(parse_signal(text, fail_closed=self.fail_closed))

    def _explain(self, signal: Signal, action: Action, delay: float,
                 known: bool) -> str:
        """A one-line, operator-facing account of the decision."""
        directive = signal.directive
        if not known:
            what = (f"unknown directive {directive!r}" if directive
                    else "malformed RECUSE sentinel")
            posture = "fail-closed" if self.fail_closed else "fail-open"
            head = f"{what} ({posture})"
        else:
            head = f"RECUSE {directive}"

        detail = []
        if signal.reason:
            detail.append(f"reason={signal.reason}")
        if signal.scope:
            detail.append(f"scope={signal.scope}")
        if signal.id:
            detail.append(f"id={signal.id}")
        if signal.ref:
            detail.append(f"ref={signal.ref}")
        suffix = f" ({'; '.join(detail)})" if detail else ""

        if action == Action.STOP:
            tail = "stopping the governed operation"
        elif action == Action.THROTTLE:
            tail = f"proceeding after a {delay:g}s delay (cap {self.max_delay:g}s)"
        elif action == Action.WARN:
            tail = "proceeding; notice surfaced to the operator"
        else:
            tail = "proceeding"
        return f"{head}{suffix} -> {action.value}: {tail}"

    # -- events ------------------------------------------------------------------
    def _finish(self, decision: Decision) -> Decision:
        decision.event = self._emit(decision)
        return decision

    def _emit(self, decision: Decision) -> dict:
        signal = decision.signal
        event = {
            "event": "recuse.decision",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "directive": decision.directive,
            "action": decision.action.value,
            "reason": decision.reason,
            "signal_id": signal.id if signal is not None else None,
            "signal_reason": signal.reason if signal is not None else None,
            "delay_seconds": decision.delay_seconds,
            "malformed": bool(signal.malformed) if signal is not None else False,
            "raw": signal.raw if signal is not None else "",
        }
        self.events.append(event)
        if self.log is not None:
            level = {
                Action.STOP: logging.ERROR,
                Action.THROTTLE: logging.WARNING,
                Action.WARN: logging.WARNING,
                Action.PROCEED: logging.DEBUG,
            }[decision.action]
            self.log.log(level, "recuse.decision %s", event)
        if self.on_event is not None:
            self.on_event(event)
        return event

    # -- execution ---------------------------------------------------------------
    def apply(self, signal: Union[Signal, "Decision", None],
              *, sleep: Callable[[float], Any] = time.sleep) -> Decision:
        """Decide about ``signal`` and *perform* the decided action.

        - ``STOP``     raises :class:`PolicyStop`.
        - ``THROTTLE`` calls ``sleep(delay_seconds)`` — delay-only, always capped.
        - ``WARN``     logs (already done by :meth:`decide`) and returns.
        - ``PROCEED``  returns immediately.

        ``sleep`` is injectable so tests (and async callers, which pass their own
        awaitable-driving shim) never actually block. A :class:`Decision` may be
        passed instead of a signal to act on an already-made decision without
        re-emitting its event.

        Returns:
            The :class:`Decision`, for every action except ``STOP``.

        Raises:
            PolicyStop: when the decided action is ``STOP``.
        """
        decision = signal if isinstance(signal, Decision) else self.decide(signal)
        if decision.action == Action.STOP:
            raise PolicyStop(decision)
        if decision.action == Action.THROTTLE and decision.delay_seconds > 0:
            # Belt and braces: clamp again at the point of use, so no mutated or
            # hand-built Decision can ever sleep past the cap.
            sleep(min(decision.delay_seconds, self.max_delay))
        return decision

    def apply_text(self, text: Any,
                   *, sleep: Callable[[float], Any] = time.sleep) -> Decision:
        """:meth:`decide_text` followed by :meth:`apply`."""
        return self.apply(self.decide_text(text), sleep=sleep)


# Convenience: the spec-default policy, safe to share (it is stateless apart from
# its event log). Construct your own Policy() when you want isolated events.
def default_policy(**kwargs: Any) -> Policy:
    """Return a :class:`Policy` with the spec's normative defaults."""
    return Policy(**kwargs)
