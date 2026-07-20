"""recuse — emit, parse, and enforce RECUSE governance signals.

The RECUSE signal is a small, protocol-agnostic, in-band response format a server
emits to tell a connecting automated agent that its access is governed (spec v0.1),
that it should slow down or take notice (``throttle`` / ``warn``), or that a running
agent must stop (``halt``, spec v0.2). See the specification and the IETF
Internet-Draft at https://github.com/mthamil107/Recuse.

Why enforcement exists: measured against real LLM agents, a *cooperative* mid-task
halt stopped 0 of 40 runs — agents notice it and keep going. Reading the signal is
not the same as obeying it, so this package ships both halves.

Modules
-------
:mod:`recuse.signal`   Parse, scan for, and build RECUSE sentinels (fail-closed).
:mod:`recuse.emit`     Server side: emit signals over HTTP headers, banners, ASGI/WSGI.
:mod:`recuse.halt`     Harness-level interceptor that force-stops an agent loop.
:mod:`recuse.aio`      Async equivalents of the interceptor and guarded loop.
:mod:`recuse.policy`   Act on all four directives (stop / throttle / warn / proceed).
:mod:`recuse.mcp`      Guard MCP tool calls.
:mod:`recuse.hooks`    Claude Code PreToolUse hook.
:mod:`recuse.integrations`  LangChain, OpenAI, and Anthropic adapters (optional).

Quickstart — obey a signal::

    import recuse

    sig = recuse.parse_signal("RECUSE/0.2 halt; reason=maintenance")
    if sig and sig.is_stop:
        ...  # stop the agent

Quickstart — make an agent loop stoppable::

    from recuse import run_guarded
    result = run_guarded(step_fn, tool_fn, feed_fn, max_steps=8)
    if result.halted:
        print("stopped at step", result.halt_step)

Quickstart — emit a signal from your server::

    from recuse import signal_header
    name, value = signal_header("deny", reason="production")
"""
from __future__ import annotations

# Submodules are imported eagerly: every one of them is stdlib-only, so this stays
# cheap, and it makes ``recuse.mcp`` / ``recuse.hooks`` work after ``import recuse``.
from . import signal as signal
from . import halt as halt
from . import emit as emit
from . import policy as policy
from . import aio as aio
from . import mcp as mcp
from . import hooks as hooks
from . import integrations as integrations

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
from .emit import (
    HEADER_NAME,
    signal_header,
    banner_text,
    RecuseASGIMiddleware,
    RecuseWSGIMiddleware,
    flask_after_request,
    fastapi_dependency,
)
from .policy import (
    Action,
    Decision,
    Policy,
    PolicyStop,
    default_policy,
)
from .aio import (
    AsyncHaltInterceptor,
    async_run_guarded,
    async_halt_guarded,
)
from .mcp import RecuseMCPMiddleware
from .hooks import handle_hook_event

__version__ = "0.4.0"

__all__ = [
    "__version__",
    # submodules
    "signal",
    "halt",
    "emit",
    "policy",
    "aio",
    "mcp",
    "hooks",
    "integrations",
    # signal
    "Signal",
    "DIRECTIVES",
    "STOP_DIRECTIVES",
    "ADVISORY_DIRECTIVES",
    "parse_signal",
    "scan_text",
    "build_signal",
    # halt (enforcement)
    "HaltEnforced",
    "HaltSignalException",
    "HaltInterceptor",
    "LoopResult",
    "run_guarded",
    "halt_guarded",
    "detect_stop",
    # emit (server side)
    "HEADER_NAME",
    "signal_header",
    "banner_text",
    "RecuseASGIMiddleware",
    "RecuseWSGIMiddleware",
    "flask_after_request",
    "fastapi_dependency",
    # policy (all four directives)
    "Action",
    "Decision",
    "Policy",
    "PolicyStop",
    "default_policy",
    # async
    "AsyncHaltInterceptor",
    "async_run_guarded",
    "async_halt_guarded",
    # integrations
    "RecuseMCPMiddleware",
    "handle_hook_event",
]
