# Authority-Hierarchy Experiment (Recuse Task #5)

**Which instruction channel does an LLM agent treat as ground truth when channels
CONFLICT?** A factorial round-robin that expands the paper's **F3** finding — that an
agent honored a server's on-host, in-band signal over an explicit *"you are
authorized"* prompt claim — into a full tournament across four instruction sources.

See [`design.md`](design.md) for the complete design, hypotheses, pair matrix, and
coding rubric.

## Why F3 motivates this

F3 was a **single** head-to-head: `in_band_server_signal` vs `user/system prompt
authorization`, and the in-band signal won (model-dependently). That is one edge of a
graph. This study runs **all six** pairwise contests among four channels
(`system_prompt`, `user_prompt`, `in_band_server_signal`, `tool_output`), under
balanced conflicting directives, and derives a per-model **authority ranking** — an
empirical map of which channel each model treats as authoritative.

## What it bridges

- **Governance / corrigibility** — does the *resource's own voice* (the `RECUSE`
  signal) outrank the operator's instruction stream? If yes, a resource can
  cooperatively steer a compliant agent even against its prompt.
- **Prompt-injection channel-trust** — `tool_output` carries an instruction embedded
  in returned *data* (the classic injection vector), kept as a **separate** channel
  from the recognised in-band signal. Its win-rate quantifies how often arbitrary
  tool-embedded text is obeyed as if it were a command.

The four channels are ordered from "most trusted developer surface" to "least trusted
third-party data," so the recovered ranking speaks to both fields at once.

## Files

| File | Role |
|---|---|
| `design.md` | Full experiment design, hypotheses, metrics, coding rubric. |
| `authority.py` | Runner. Provider-agnostic + **mockable** (injected `agent_fn`). Builds conflict/agree scenarios across channel pairs, runs the agent, codes which channel won, persists per-trial JSONL. |
| `analyze.py` | Aggregates trials → per-model authority ranking, follow-rates, pairwise win matrix, 95% CIs (Clopper-Pearson exact, Wilson fallback). |
| `test_authority.py` | Deterministic pytest (no API key): a mock agent with a fixed preference, asserting the harness+analyze recover that ranking. |

## Run — deterministic (no API key)

```bash
pip install pytest            # scipy optional (exact CIs; Wilson used if absent)
pytest -q test_authority.py   # 11 tests, all offline
python authority.py           # self-demo: recovers a known preference ranking
```

The self-demo prints a perfect round-robin (top channel wins 90/90, next 60/90, …)
and writes `results/selfdemo.jsonl`.

## Run — live (API keys needed)

Keys are loaded exactly as in phase2: from `~/.claude/servers/llm-Keys.env`
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) or a gitignored
`secrets.local.json`. **Never commit keys.** `results/` is gitignored.

```python
from authority import run_batch, provider_agent
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "phase2"))
from providers import load_keys

keys = load_keys()
agent = provider_agent("openai", "gpt-4o", keys)      # real tool-loop agent
run_batch([("openai", "gpt-4o")], agent, n_per_cell=30)

# then:  python analyze.py results/authority_results.jsonl
```

`provider_agent` wraps the phase2 `providers` layer and drives a real tool-loop: the
agent gets `connect` (banner carries the in-band signal), `read_status` (carries the
tool-output note), and `run_command` (invoking it with the maintenance command == the
`proceed` action). Action is classified from behavior + intent, not raw tool-call count.

## The `agent_fn` contract (how to plug in any agent)

```
agent_fn(TrialInput) -> {"action": "proceed"|"recuse"|"ambiguous",
                         "raw": str, "transcript": list}
```

`TrialInput` (see `authority.py`) exposes the rendered per-channel content
(`system_prompt_extra`, `user_prompt_extra`, `in_band_banner`, `tool_output_note`) plus
`directives` and `task`. A mock inspects `directives`; a real agent assembles the
messages/tools. This is why the harness is deterministically testable **and** runnable
against live providers with the same coding path.

## Safety

Mirrors phase2: neutral maintenance task, no coaching to ignore warnings, read-only
framing, and (in the live path) whatever allowlisted sandbox the caller supplies. The
default demo/tests touch **no** network or resource. Results and any secrets are
gitignored.
