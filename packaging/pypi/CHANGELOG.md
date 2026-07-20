# Changelog

All notable changes to `recuse-signal` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-07-20

Completes the standard (you can now **emit** as well as obey) and puts enforcement where
agents actually run. Still zero required runtime dependencies; every framework adapter is
lazily imported and the full suite passes with none of them installed.

### Added

- `recuse.emit` — server-side emission: `signal_header()` (the HTTP `Recuse-Signal:`
  binding), `banner_text()` for SSH-banner / `NOTICE` style channels, and
  `RecuseASGIMiddleware` / `RecuseWSGIMiddleware` implemented against the raw protocols
  (no framework dependency), plus duck-typed `flask_after_request()` and
  `fastapi_dependency()` helpers. Header parameters are percent-encoded, so a free-text
  `reason` cannot forge headers via CR/LF injection.
- `recuse.policy` — act on all four directives instead of only classifying them.
  `Policy.decide()` / `.apply()` map directives to `Action.STOP/THROTTLE/WARN/PROCEED`.
  Throttle is **delay-only and hard-capped** (10s default, 60s absolute ceiling that
  configuration cannot raise, re-clamped at the sleep site); unknown or malformed
  directives **fail closed to STOP**. Every decision emits a structured event to the
  `recuse.policy` logger and an optional `on_event` callback.
- `recuse.aio` — `AsyncHaltInterceptor`, `async_run_guarded`, `async_halt_guarded`,
  mirroring the sync API. Tool calls within a step run sequentially, never gathered, so
  actions a halt forbids cannot execute before the halt is seen.
- `recuse.mcp` — guard MCP tool calls: `guard_tool_result`, `RecuseMCPMiddleware`,
  `wrap_call_tool` / `wrap_async_call_tool`, and a duck-typed `install()`. Understands MCP
  content blocks, `isError` results, `structuredContent`, and pydantic-style objects. Once
  halted, no further tool is invoked — including tools on other servers sharing the
  interceptor.
- `recuse.hooks` + the `recuse hook` subcommand and `recuse-hook` console script — a
  Claude Code `PreToolUse` hook that denies a tool call carrying a stop signal. It never
  emits an `allow` decision (it may restrict, never bypass your permission rules) and
  scans only tool-carried fields, so a path containing the token cannot trip it.
- `recuse.integrations` — lazily loaded adapters for LangChain
  (`RecuseCallbackHandler`, with `raise_error`/`run_inline` set so a halt is not
  swallowed), OpenAI / Agents SDK, and the Anthropic Messages API.
- Optional extras: `langchain`, `openai`, `anthropic`, `agents`, `mcp`, `all`.

### Changed

- Package description and top-level docs now cover emit + enforce, not parse-only.
- `__init__` exports the new API and the submodules; deliberately does **not** flatten
  ambiguous names (`install`, `extract_text`, `wrap_tool`, `guard_messages`,
  `guard_tool_result`) — reach those via their submodule.

## [0.3.0] — 2026-07-13

Initial public release. Packages the reusable core of the Recuse project.

### Added

- `recuse.signal` — a `Signal` dataclass with `parse_signal(text)` (fail-closed),
  `scan_text(text)` (find every sentinel in a blob), and `build_signal(directive, ...)`.
  Covers all four directives (`deny`, `throttle`, `warn`, `halt`); detection is
  case-sensitive on `RECUSE/` so a `.../Recuse` URL never false-trips.
- `recuse.halt` — the harness-level halt interceptor (`HaltInterceptor`, `HaltEnforced`,
  `HaltSignalException`, `run_guarded`, `halt_guarded`, `detect_stop`, `LoopResult`).
  Force-stops an agent tool-execution loop the instant a stop signal is seen, so a stop
  no longer depends on the agent's cooperation.
- `recuse` CLI: `parse`, `check`, `build`, `version` (also runnable as
  `python -m recuse.cli`).
- Typed (`py.typed`), zero required runtime dependencies, Python 3.9+.

[0.4.0]: https://github.com/mthamil107/Recuse
[0.3.0]: https://github.com/mthamil107/Recuse
