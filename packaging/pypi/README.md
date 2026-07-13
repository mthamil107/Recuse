# recuse-signal

Parse and build **RECUSE** governance signals, and **force-stop an LLM agent
mid-flight** when a stop signal arrives — even when the agent won't stop on its own.

`recuse-signal` is the reusable core of the [Recuse project](https://github.com/mthamil107/Recuse):
a small, protocol-agnostic, in-band response format that a server emits to tell a
connecting automated agent that its access is governed, plus a harness-level
interceptor that *enforces* the stop instead of relying on the agent's cooperation.

- **Zero runtime dependencies** (Python standard library only).
- **Python 3.9+.**
- **Apache-2.0** licensed.

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

## CLI

```bash
recuse parse "RECUSE/0.2 halt; reason=maintenance"   # detect + print directive/JSON
recuse parse "RECUSE/0.2 halt" --json
recuse check path/to/tool-output.log                 # scan a file; non-zero exit on a stop
recuse build halt --reason operator-request --id abc-123
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
