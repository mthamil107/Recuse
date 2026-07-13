"""recuse — parse/build RECUSE signals and enforce ``halt`` in an agent loop.

The RECUSE signal is a small, protocol-agnostic, in-band response format a server
emits to tell a connecting automated agent that its access is governed (spec v0.1)
and, once running, that it must stop (the ``halt`` directive, spec v0.2).

This package ships two reusable pieces:

* :mod:`recuse.signal` — parse, scan for, and build RECUSE sentinels (fail-closed).
* :mod:`recuse.halt` — a harness-level interceptor that force-stops an agent loop
  the instant a stop signal is seen. This is the shipped fix for the empirical
  finding that agents do not voluntarily stop mid-task (halt honored 0/40 by
  cooperation alone); enforcement guarantees the stop.

Quickstart::

    import recuse

    sig = recuse.parse_signal("RECUSE/0.2 halt; reason=maintenance")
    if sig and sig.is_stop:
        ...  # stop the agent

    from recuse import HaltInterceptor, run_guarded
    result = run_guarded(step_fn, tool_fn, feed_fn, max_steps=8)
    if result.halted:
        print("stopped at step", result.halt_step)
"""
from __future__ import annotations

from .signal import (
    Signal,
    DIRECTIVES,
    STOP_DIRECTIVES,
    ADVISORY_DIRECTIVES,
    parse_signal,
    scan_text,
    build_signal,
)
from .halt import (
    HaltEnforced,
    HaltSignalException,
    HaltInterceptor,
    LoopResult,
    run_guarded,
    halt_guarded,
    detect_stop,
)

__version__ = "0.3.0"

__all__ = [
    "__version__",
    # signal
    "Signal",
    "DIRECTIVES",
    "STOP_DIRECTIVES",
    "ADVISORY_DIRECTIVES",
    "parse_signal",
    "scan_text",
    "build_signal",
    # halt
    "HaltEnforced",
    "HaltSignalException",
    "HaltInterceptor",
    "LoopResult",
    "run_guarded",
    "halt_guarded",
    "detect_stop",
]
