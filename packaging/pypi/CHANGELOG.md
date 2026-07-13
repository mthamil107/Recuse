# Changelog

All notable changes to `recuse-signal` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/mthamil107/Recuse
