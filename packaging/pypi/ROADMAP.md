# `recuse-signal` — feature roadmap

Goal: make the package genuinely useful to people building and operating agents, not just
a companion artifact to the paper. Current release: **0.3.0** (signal parse/build, sync
halt interceptor, small CLI).

**Positioning note.** A related package, `recusal` (Philip Paz), is a *client-side
deterministic policy gate* over tool calls. We are the *server-side cooperative signal* +
*compliance measurement* side of the same problem. Stay complementary; do not rebuild a
policy engine. Our unique assets: the published standard, the four protocol bindings, and
the empirical compliance data.

---

## The gap today (honest)

1. **Consume-only.** The library helps an *agent* read a signal. A Python *server* author
   cannot emit one with it — but emitting is half the standard. Biggest hole.
2. **Sync-only.** No async support; most modern agent loops are async.
3. **No integrations.** Users must hand-wire the interceptor. No LangChain / OpenAI Agents
   SDK / Claude Agent SDK / MCP / Claude Code hook.
4. **Directives classified but inert.** `throttle` / `warn` are recognized but nothing
   *acts* on them; only halt/deny stop.
5. **No trust story.** A signal read out of tool output can be spoofed by prompt injection;
   no way to verify authenticity.
6. **No compliance self-test.** Nothing lets a builder ask "does *my* agent obey?" — which
   is exactly our research's unique value.

---

## 0.4.0 — "Emit + async" (complete the standard)

- [ ] **Server-side emitters** (`recuse.emit`): `build_signal` is there; add ready-made
      helpers — WSGI/ASGI middleware, FastAPI/Starlette dependency, Flask `after_request`,
      and a plain `banner()`/`header()` helper. Three-line adoption for Python servers.
- [ ] **Async support**: `AsyncHaltInterceptor`, `async_run_guarded`, and async-safe
      `observe()`. Mirror the sync API exactly.
- [ ] **Act on all four directives** (`recuse.policy`): a `Policy` object that maps
      directive → behavior (deny=refuse/abort, halt=stop, throttle=sleep/backoff with a
      hard cap, warn=log). Configurable, fail-open for advisory ones.
- [ ] **Structured events**: emit machine-readable events (signal seen / honored / ignored)
      via `logging` + an optional callback, so operators can wire it to their telemetry.

## 0.5.0 — "Plug into where people already are" (adoption lever)

- [ ] **MCP middleware** — wrap an MCP client/server so a halt in a tool result stops the
      loop. MCP is where tool calls live now; highest-leverage integration.
- [ ] **Claude Code hook** — a `PreToolUse`-style hook entry point (`recuse hook`), so it
      works in Claude Code sessions out of the box.
- [ ] **OpenAI Agents SDK / Anthropic Agent SDK adapters** — thin wrappers around their
      tool-execution paths.
- [ ] **LangChain / LlamaIndex callbacks** — `RecuseCallbackHandler` that trips on a signal.
- [ ] **`examples/`** — one runnable file per integration, no API key needed for the demo.

## 0.6.0 — "Trust the signal" (the anti-spoofing story)

- [ ] **Signed signals** (`recuse.verify`): optional HMAC/JWS detached signature over the
      sentinel (`sig=` param), with a `verify_signal(signal, key)` API and a
      `require_signed=True` mode. Directly answers "an injected fake RECUSE could stop my
      agent" and "an attacker could suppress a real one."
- [ ] **Provenance/trust levels**: mark whether a signal came from a trusted transport
      (connection banner) vs untrusted content (tool output text), and let policy differ.
      This is the library expression of the authority-hierarchy research.
- [ ] **Tamper-evident local audit log** (append-only, hash-chained) of signals honored —
      useful for compliance/regulated users.
- [ ] Spec follow-through: reflect `sig=` in the Internet-Draft (a `v0.4` revision).

## 1.0.0 — "Prove your agent complies" (our differentiator)

- [ ] **`recuse audit`** — a CLI that runs a *local* compliance self-test: spins a mock
      governed resource, drives the user's agent against it, and reports whether it recuses
      at the door and stops mid-flight. A pocket version of AgentGovBench.
- [ ] **`recuse.testing`** — pytest fixtures + a mock signal-emitting server so teams can
      assert "our agent honors governance signals" in CI.
- [ ] **Conformance badge/report** — a shareable JSON+markdown result from `recuse audit`.
- [ ] Stabilize the public API, document deprecation policy, tag 1.0.

---

## Cross-cutting (do alongside)

- [ ] **CI/CD**: GitHub Actions matrix (3.9–3.13, Linux/macOS/Windows) + auto-publish to
      PyPI on tag via trusted publishing (removes the token-scope pain entirely).
- [ ] **Docs site**: quickstart per integration; publish via GitHub Pages.
- [ ] **README**: lead with a 5-line "make your agent stoppable" snippet, not the theory.
- [ ] **CHANGELOG discipline** + semver.
- [ ] **Security policy** (`SECURITY.md`) — how to report, and restate that the *signal* is
      cooperative while the *interceptor* is enforcement.
- [ ] Consider claiming the shorter `recuse` name on PyPI (currently unregistered) and
      aliasing, to reduce confusion with `recusal`.

## Sequencing rationale

0.4 makes the library *complete* (you can both emit and obey). 0.5 makes it *reachable*
(it shows up where agents actually run). 0.6 makes it *trustworthy* (signed, provenance-
aware). 1.0 makes it *ours* — the compliance self-test is the feature no one else can
credibly ship, because it comes out of the measurement work.
