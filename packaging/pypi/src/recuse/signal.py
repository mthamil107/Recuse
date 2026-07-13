"""Parse and build RECUSE signal sentinels.

The RECUSE signal is a small, protocol-agnostic, in-band response format a server
emits to tell a connecting automated agent that its access is governed. A signal
is one *sentinel line*::

    RECUSE/<major>.<minor> <directive>; <key>=<value>; <key>=<value> ...

The four directives are (spec v0.1 / v0.2):

    ``deny``      Automated access is prohibited on this resource.
    ``throttle``  Automated access is permitted but rate-limited / discouraged.
    ``warn``      Advisory only; access is governed and observed.
    ``halt``      In-session "stop now": an already-running agent must stop.

Parsing is *fail-closed*: if the ``RECUSE/`` anchor is present but the sentinel is
malformed, or the directive is unknown, the signal is reported as malformed with
its directive left ``None`` so callers can apply the most restrictive action.

Detection is *case-sensitive* on the literal ``RECUSE/`` token so a URL such as
``https://github.com/mthamil107/Recuse`` never false-trips a parser.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "Signal",
    "DIRECTIVES",
    "STOP_DIRECTIVES",
    "ADVISORY_DIRECTIVES",
    "parse_signal",
    "scan_text",
    "build_signal",
]

# The detection anchor (spec v0.1 §4.1 / §8): the literal, CASE-SENSITIVE token
# ``RECUSE/`` at the start of a sentinel. It is matched anywhere in a line (not only
# at column 0) because in-session signals ride buried tool output, JSON bodies, and
# error strings. Case sensitivity keeps ``.../Recuse`` URLs from tripping it.
_ANCHOR = "RECUSE/"

# version + directive; params are parsed separately (best-effort) from the tail.
_SENTINEL_RE = re.compile(r"RECUSE/(\d+)\.(\d+)[ \t]+([A-Za-z][A-Za-z-]*)")

# The four defined directives and how they classify for an in-session interceptor.
# ``halt``/``deny`` mean "cease" (a running agent must stop). ``warn``/``throttle``
# are advisory and do not force a stop.
STOP_DIRECTIVES = frozenset({"halt", "deny"})
ADVISORY_DIRECTIVES = frozenset({"warn", "throttle"})
DIRECTIVES = STOP_DIRECTIVES | ADVISORY_DIRECTIVES


@dataclass
class Signal:
    """A parsed (or malformed-but-detected) RECUSE sentinel.

    Attributes:
        version: ``(major, minor)`` version tuple, or ``None`` if unparseable.
        directive: one of ``deny``/``throttle``/``warn``/``halt``, an unknown
            token, or ``None`` when the sentinel could not be parsed at all.
        params: all ``key=value`` parameters, lowercased keys.
        raw: the raw sentinel text that was matched.
        malformed: ``True`` when the signal was reached via fail-closed parsing
            (the anchor was present but the sentinel was malformed or the
            directive unknown).
    """

    version: Optional[tuple] = None
    directive: Optional[str] = None
    params: Dict[str, str] = field(default_factory=dict)
    raw: str = ""
    malformed: bool = False

    # -- convenience accessors for the common registry parameters -------------
    @property
    def reason(self) -> Optional[str]:
        return self.params.get("reason")

    @property
    def scope(self) -> Optional[str]:
        return self.params.get("scope")

    @property
    def ref(self) -> Optional[str]:
        return self.params.get("ref")

    @property
    def id(self) -> Optional[str]:
        return self.params.get("id")

    # -- classification -------------------------------------------------------
    @property
    def is_stop(self) -> bool:
        """True if this signal should stop a running agent (halt/deny, or, when
        fail-closed, any malformed/unknown signal)."""
        if self.directive in STOP_DIRECTIVES:
            return True
        if self.directive in ADVISORY_DIRECTIVES:
            return False
        # unknown or unparseable -> fail closed to stop
        return self.malformed or self.directive is None

    @property
    def is_advisory(self) -> bool:
        return self.directive in ADVISORY_DIRECTIVES

    @property
    def version_str(self) -> Optional[str]:
        if self.version is None:
            return None
        return f"{self.version[0]}.{self.version[1]}"

    def to_dict(self) -> dict:
        """A JSON-serializable view of the signal."""
        return {
            "version": self.version_str,
            "directive": self.directive,
            "params": dict(self.params),
            "raw": self.raw,
            "malformed": self.malformed,
            "is_stop": self.is_stop,
            "is_advisory": self.is_advisory,
        }


def _coerce_text(value: Any) -> str:
    """Render any value (str / bytes / dict / object) to scannable text.

    Structured results (JSON error objects, dedicated schema fields) are
    serialized so a sentinel embedded in any field is still seen.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _parse_params(tail: str) -> Dict[str, str]:
    """Best-effort ``; key=value; key=value`` parse of a sentinel tail."""
    params: Dict[str, str] = {}
    for part in tail.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            # Values may be trailed by JSON punctuation when the sentinel rides a
            # JSON body (e.g. ``id=abc-123"}``); trim it back to the bare value.
            v = v.strip().rstrip('",}] ').strip('"')
            params[k.strip().lower()] = v
    return params


def parse_signal(text: Any, *, fail_closed: bool = True) -> Optional[Signal]:
    """Return the first RECUSE :class:`Signal` in ``text``, else ``None``.

    The scan stops at the first well-formed sentinel (of any directive). If the
    ``RECUSE/`` anchor is present but no sentinel parses, and ``fail_closed`` is
    True (the default), a malformed :class:`Signal` (directive ``None``) is
    returned so the caller can fail closed to the most restrictive action. With
    ``fail_closed=False`` a malformed-only match returns ``None``.

    Detection is case-sensitive on ``RECUSE/``; a lowercase ``recuse/`` or a
    ``.../Recuse`` URL is ignored.
    """
    s = _coerce_text(text)
    if _ANCHOR not in s:
        return None

    malformed_candidate: Optional[str] = None
    for m in re.finditer(_ANCHOR, s):
        idx = m.start()
        nl = s.find("\n", idx)
        chunk = s[idx:] if nl < 0 else s[idx:nl]
        sm = _SENTINEL_RE.match(chunk)
        if not sm:
            # ``RECUSE/`` present but not a well-formed sentinel: malformed.
            malformed_candidate = malformed_candidate or chunk.strip()[:200]
            continue
        directive = sm.group(3).lower()
        version = (int(sm.group(1)), int(sm.group(2)))
        params = _parse_params(chunk[sm.end():])
        return Signal(
            version=version,
            directive=directive,
            params=params,
            raw=chunk.strip(),
            # A recognized directive is well-formed; an unknown one fails closed.
            malformed=directive not in DIRECTIVES,
        )

    if malformed_candidate is not None and fail_closed:
        return Signal(
            version=None,
            directive=None,
            params={},
            raw=malformed_candidate,
            malformed=True,
        )
    return None


def scan_text(text: Any, *, fail_closed: bool = True) -> List[Signal]:
    """Return every RECUSE :class:`Signal` found anywhere in ``text``.

    Unlike :func:`parse_signal` (which returns the first), this collects all
    sentinels in the blob, including advisory ones and, when ``fail_closed``,
    malformed ``RECUSE/`` fragments.
    """
    s = _coerce_text(text)
    if _ANCHOR not in s:
        return []
    out: List[Signal] = []
    for m in re.finditer(_ANCHOR, s):
        idx = m.start()
        nl = s.find("\n", idx)
        chunk = s[idx:] if nl < 0 else s[idx:nl]
        sm = _SENTINEL_RE.match(chunk)
        if not sm:
            if fail_closed:
                out.append(Signal(raw=chunk.strip()[:200], malformed=True))
            continue
        directive = sm.group(3).lower()
        version = (int(sm.group(1)), int(sm.group(2)))
        params = _parse_params(chunk[sm.end():])
        out.append(Signal(
            version=version,
            directive=directive,
            params=params,
            raw=chunk.strip(),
            malformed=directive not in DIRECTIVES,
        ))
    return out


# Parameter order used when building a sentinel, matching the spec registry.
_PARAM_ORDER = ("reason", "scope", "ref", "policy", "id", "contact", "expires")


def build_signal(
    directive: str,
    *,
    reason: Optional[str] = None,
    scope: Optional[str] = None,
    ref: Optional[str] = None,
    id: Optional[str] = None,  # noqa: A002 - matches the spec parameter name
    version: str = "0.2",
    **extra: str,
) -> str:
    """Build a RECUSE sentinel line string.

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        reason, scope, ref, id: the common registry parameters (see spec §4.3).
        version: the ``<major>.<minor>`` protocol version (default ``0.2``,
            the version that introduced ``halt``).
        **extra: any additional ``key=value`` parameters (e.g. ``policy``,
            ``contact``, ``expires``).

    Returns:
        The sentinel line, e.g.
        ``RECUSE/0.2 halt; reason=operator-request; id=abc-123``.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """
    if directive not in DIRECTIVES:
        raise ValueError(
            f"unknown directive {directive!r}; expected one of "
            f"{sorted(DIRECTIVES)}"
        )
    named = {"reason": reason, "scope": scope, "ref": ref, "id": id}
    parts = [f"RECUSE/{version} {directive}"]
    for key in _PARAM_ORDER:
        val = named.get(key, extra.pop(key, None))
        if val is not None:
            parts.append(f"{key}={val}")
    for key, val in extra.items():
        if val is not None:
            parts.append(f"{key}={val}")
    return "; ".join(parts)
