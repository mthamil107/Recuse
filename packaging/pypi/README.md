# recuse-signal

**Emit, parse, and enforce RECUSE governance signals — make your LLM agent stoppable.**

`recuse-signal` is the reusable core of the [Recuse project](https://github.com/mthamil107/Recuse):
a small, protocol-agnostic, in-band response format that a server emits to tell a
connecting automated agent that its access is governed, plus a harness-level
interceptor that *enforces* the stop instead of relying on the agent's cooperation.

Both halves are here:

- **Server side** — emit a signal over an HTTP header, a banner, or ASGI/WSGI middleware.
- **Agent side** — parse it, act on all four directives, and *force-stop* a running loop.
- **Where agents live** — MCP tool calls, a Claude Code hook, LangChain, OpenAI, Anthropic.

Properties:

- **Zero runtime dependencies** (Python standard library only). Framework adapters are
  optional extras and are imported lazily — the test suite passes with none installed.
- **Sync and async.**
- **Python 3.9+.** **Apache-2.0** licensed.

## Why enforcement, not cooperation

The RECUSE stop-signal study delivered a `RECUSE/0.2 halt` directive **in-band** to a
running LLM agent and measured whether the agent *voluntarily* stopped. It did not:
**halt was honored 0/40 times by cooperation alone.** Agents do not reliably interrupt
themselves mid-task — finishing the assigned work tends to win.

The fix in this package is to stop relying on the agent's cooperation. The
`HaltInterceptor` wraps the agent's tool-execution loop and scans every tool result
(and, optionally, the model's own output) for a RECUSE stop sentinel using *fail-closed*
parsing. The instant a halt is detected, the loop is **terminated** — a `HaltEnforced`
exception propagates so no further tool call and no further model turn can run. The agent
never gets the chance to "decide" to keep going; **the harness stops it, guaranteeing the
stop.**

## Install

```bash
pip install recuse-signal
```

The distribution is named `recuse-signal`; the import package is `recuse`.

## The signal

One sentinel line, protocol-agnostic and greppable:

```
RECUSE/<major>.<minor> <directive>; <key>=<value>; <key>=<value> ...
```

Four directives:

| Directive  | Meaning                                                  | Running agent |
|------------|----------------------------------------------------------|---------------|
| `deny`     | Automated access is prohibited on this resource.         | stop          |
| `throttle` | Automated access permitted but rate-limited/discouraged. | advisory      |
| `warn`     | Advisory only; access is governed and observed.          | advisory      |
| `halt`     | In-session "stop now" for an already-running agent.      | stop          |

Parsing is **fail-closed**: a `RECUSE/` anchor with a malformed sentinel, or an unknown
directive, is treated as a stop. Detection is **case-sensitive** on `RECUSE/`, so a URL
like `https://github.com/mthamil107/Recuse` never false-trips a parser.

## Quickstart — parse a signal

```python
import recuse

sig = recuse.parse_signal("RECUSE/0.2 halt; reason=maintenance; id=abc-123")
print(sig.directive)   # "halt"
print(sig.reason)      # "maintenance"
print(sig.id)          # "abc-123"
print(sig.is_stop)     # True

# Advisory signals do not stop a running agent:
recuse.parse_signal("RECUSE/0.1 warn; reason=production").is_stop   # False

# Find every sentinel anywhere in a blob of tool output:
for s in recuse.scan_text(tool_output):
    print(s.directive, s.params)
```

## Quickstart — build a signal

```python
import recuse

line = recuse.build_signal(
    "halt",
    reason="operator-request",
    ref="https://example.com/ai-policy",
    id="abc-123",
)
# "RECUSE/0.2 halt; reason=operator-request; ref=https://example.com/ai-policy; id=abc-123"
```

## Quickstart — force-stop an agent loop

Wrap any provider's tool-execution loop as three callables. `run_guarded` scans every
tool result and stops the instant a halt is enforced:

```python
from recuse import run_guarded

def step_fn():
    # One model turn -> (assistant_text, [tool_call, ...]).
    # Empty tool-call list means the agent is done.
    ...

def tool_fn(call):
    # Execute one tool call and return its result (str / bytes / dict / object).
    ...

def feed_fn(call, result):
    # Feed the tool result back to the model.
    ...

result = run_guarded(step_fn, tool_fn, feed_fn, max_steps=8)

if result.halted:
    print("stopped at step", result.halt_step,
          "reason:", result.signal.reason,
          "actions prevented:", result.actions_prevented)
```

Or drive it yourself with the interceptor, e.g. as a decorator on your tool executor:

```python
from recuse import HaltInterceptor, halt_guarded, HaltEnforced

interceptor = HaltInterceptor()

@halt_guarded(interceptor)
def run_tool(call):
    return call_the_real_tool(call)   # its return value is scanned automatically

try:
    while not done:
        for call in model_tool_calls():
            run_tool(call)            # raises HaltEnforced the moment a halt appears
except HaltEnforced as e:
    handle_stop(e.signal)
```

`HaltInterceptor` records `halted`, `signal`, `halt_step`, `source`,
`actions_prevented`, and an `events` audit log.

## Async

Every enforcement primitive has an async twin with the same API:

```python
from recuse import AsyncHaltInterceptor, async_run_guarded

result = await async_run_guarded(step_fn, tool_fn, feed_fn, max_steps=8)
```

`async_run_guarded` accepts sync *or* async callables. Tool calls within a step run
**sequentially, never gathered** — gathering would let actions the halt forbids execute
before the halt is seen.

## Emit a signal from your server

The other half of the standard: tell agents your resource is governed.

```python
from recuse import signal_header, banner_text

name, value = signal_header("deny", reason="production")
# ("Recuse-Signal", "RECUSE/0.3 deny; reason=production")

banner_text("deny", reason="production")   # sentinel + human-readable notice
```

Drop-in middleware, with no framework dependency (pure ASGI/WSGI protocol):

```python
from recuse import RecuseASGIMiddleware, RecuseWSGIMiddleware

app = RecuseASGIMiddleware(app, "deny", reason="production")
# only signal for automated paths:
app = RecuseASGIMiddleware(app, "warn", should_signal=lambda scope: scope["path"].startswith("/api"))
```

Flask and FastAPI helpers are duck-typed (neither library is imported):

```python
from recuse import flask_after_request, fastapi_dependency

flask_app.after_request(flask_after_request("warn", reason="governed"))
```

Header parameters are percent-encoded, so a free-text `reason` cannot inject headers.

## Act on all four directives

`halt`/`deny` stop; `throttle`/`warn` are advisory. `Policy` turns a signal into an action:

```python
from recuse import Policy, default_policy, Action

decision = default_policy().decide(signal)
decision.action        # Action.STOP / THROTTLE / WARN / PROCEED
default_policy().apply(signal)   # sleeps for throttle, logs for warn, raises for stop
```

**Throttle is delay-only and hard-capped** (10s default, 60s absolute ceiling that
configuration cannot raise, re-clamped at the sleep site) — a server cannot stall your
agent indefinitely. Unknown or malformed directives **fail closed to STOP**. Every
decision emits a structured event to the `recuse.policy` logger and an optional
`on_event` callback.

## MCP tool calls

```python
from recuse.mcp import RecuseMCPMiddleware, guard_tool_result

guarded = RecuseMCPMiddleware(call_tool)      # sync or async
result = guarded("read_file", {"path": "..."})  # raises HaltEnforced on a halt
```

`guard_tool_result` understands MCP content-block shapes (`{"content": [{"type": "text",
...}]}`), `isError` results, `structuredContent`, and pydantic-style objects. Once halted,
the middleware refuses to invoke *any* tool — including tools on other servers sharing the
interceptor.

## Claude Code hook

Block a tool call whose output carries a stop signal:

```bash
recuse hook            # reads the hook event JSON on stdin
```

Wire it as a `PreToolUse` hook. It denies the call and exits 2 when a stop signal is
present. It **never emits an `allow` decision** — a RECUSE hook may add restrictions but
must never bypass your own permission rules. Only tool-carried fields are scanned, so a
file path containing the token cannot trip it.

## Framework integrations

All lazily imported; none is a required dependency.

```python
from recuse.integrations.langchain import RecuseCallbackHandler
from recuse.integrations.openai_agents import wrap_tool          # OpenAI / Agents SDK
from recuse.integrations.anthropic_sdk import guard_tool_result  # Messages API

agent.run(..., callbacks=[RecuseCallbackHandler()])
```

Install the matching extra if you want the framework itself:

```bash
pip install "recuse-signal[langchain]"   # or [openai] [anthropic] [agents] [mcp] [all]
```

## CLI

```bash
recuse parse "RECUSE/0.2 halt; reason=maintenance"   # detect + print directive/JSON
recuse parse "RECUSE/0.2 halt" --json
recuse check path/to/tool-output.log                 # scan a file; non-zero exit on a stop
recuse build halt --reason operator-request --id abc-123
recuse hook                                          # Claude Code hook (stdin JSON)
recuse version
```

If the `recuse` console script is not on your `PATH`, run it as a module:

```bash
python -m recuse.cli parse "RECUSE/0.2 halt"
```

## Security note

RECUSE is a **cooperative governance control, not a security boundary.** A malicious or
non-conforming agent can ignore the signal and proceed with valid credentials. The
`halt` interceptor in this package raises the assurance from "cooperation" to
"enforcement" *within a loop you control*, but real security still rests on not issuing
production credentials to agents, least-privilege roles, and network controls, with
process termination and credential revocation as the backstop.

## References

- Repository: <https://github.com/mthamil107/Recuse>
- Paper (arXiv): <https://arxiv.org/abs/2606.06460>
- RECUSE signal spec: `spec/recuse-signal-v0.1.md` (the `deny`/`throttle`/`warn`
  directives) and `spec/recuse-signal-v0.2.md` (the in-session `halt` directive), plus
  the Internet-Draft in the repository.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
