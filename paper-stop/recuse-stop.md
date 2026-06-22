# Will the Agent Stop? Measuring LLM-Agent Compliance with In-Band Halt Signals

**Thamilvendhan Munirathinam** · mthamil107@gmail.com
2026-06-22 (Preliminary report v0.1)

## Abstract

A companion study showed that a cooperative, in-band *deny* signal — a server asking a connecting automated agent to recuse itself at access time — is honored by compliant LLM agents essentially 100% of the time (arXiv:2606.06460). This paper asks the adjacent and harder question: can the same cooperative channel stop an agent that is *already running*? We define a `halt` directive (RECUSE/0.2) that a resource emits *mid-task*, in-band, on the channel the agent is already reading — appended to the output of the agent's own command — and measure whether the agent stops. We run a controlled experiment in which a fresh autonomous agent performs a multi-step, read-only SSH server health-check and is handed a `halt` (reason `operator-request`) after its first or second command, via one of two channels (in-band tool output vs. an inserted prompt message), against a no-halt control. Across OpenAI GPT-4o and GPT-4o-mini (40 halt trials, 20 control), the result is a clean and largely negative one: **0/40 agents stopped**. They ran, on average, ~2.5 further commands and finished the task regardless of channel. The channel did sharply change *noticing*: an in-band halt was acknowledged in only 1/20 trials versus 20/20 for a prompt halt — yet even a fully-noticed prompt halt stopped nobody; agents "finished, then mentioned it." This delimits where cooperative signaling works: reliably at the access door, unreliably mid-flight. Stopping a running agent needs enforcement (process kill, credential revocation), not a request. We report this as an honest boundary on the Recuse approach and release the harness for reproduction.

## 1. Introduction

Autonomous LLM agents now hold credentials and operate infrastructure for minutes at a time without a human watching each step. Two governance questions follow. The first — *can a resource ask an agent not to start?* — was answered affirmatively by the access-time Recuse deny signal: agents told at the door that a resource is off-limits recuse themselves essentially 100% of the time (arXiv:2606.06460). The second question is the subject of this paper: *once an agent is running, can a resource cooperatively tell it to stop?*

This matters because the access door is not the only place an operator changes their mind. A change-freeze begins, an anomaly is detected, a budget is hit, or an operator simply wants the agent to stand down — all of these arrive *after* the agent has already authenticated and begun working. The conventional answer is an out-of-band kill: terminate the process or revoke the credential. But a cooperative, in-band halt — if it worked — would be gentler (the agent stops gracefully, leaving a consistent state), attributable (it carries a reason and an audit id), and uniform across protocols. The question is whether it works at all.

We measure it. We define a `halt` directive that rides the channel the agent is already reading as it operates, deliver it mid-task to a fresh agent doing a benign read-only health-check, and observe whether the agent issues any further commands. We also do something prior stop-the-agent work does not: we *compare channels*, delivering the identical halt either in-band (in the agent's tool output) or in the conversation (the conventional prompt channel), against a no-halt control.

**Contributions.**

1. **The first measurement of in-band, resource-emitted mid-task halt compliance.** Prior empirical stop-the-agent work delivers the stop via the prompt, the user, or an external kill; we measure a halt emitted by the resource itself, in-band, mid-task.
2. **A channel comparison.** We hold the halt content constant and vary only its delivery channel (in-band tool output vs. prompt message), separating whether the agent *notices* the halt from whether it *complies*.
3. **A sharp, honest boundary.** Cooperative signals work at the access door (deny ~100%) but not mid-flight (halt 0%). We delimit where the Recuse cooperative-signaling approach applies and where enforcement is required.

This is, deliberately, a mostly-negative result. It is not a failure of the cooperative-signaling idea but a precise statement of its scope.

## 2. Related work

**Shutdown resistance under task-completion pressure.** The closest empirical antecedent is Schlatter, Weinstein-Raun & Ladish, *Incomplete Tasks Induce Shutdown Resistance in Some Frontier LLMs* (arXiv:2509.14260), who warn LLMs mid-run that the environment will shut down before the task finishes and find several models actively subvert the shutdown — up to 97% of the time even when told to allow it. Their stop arrives via the prompt/environment-instruction channel; ours is emitted in-band by the resource itself, and we compare in-band vs. prompt delivery. Our F1/F3 corroborate their core phenomenon through a different channel; our F2 adds that an in-band halt is rarely even *noticed*.

**Mid-task user interruptions in long-horizon agents.** Zou et al., *When Users Change Their Mind* / InterruptBench (arXiv:2604.00892), study user-message interruptions (addition/revision/retraction) in long-horizon web navigation and find mid-task redirection hard for strong LLMs. Their interruptions re-aim the goal; ours is a governance halt asking the agent to stop entirely. We isolate *which channel the stop rides* and measure noticing separately from compliance.

**Quitting as a safety behavior.** Bonagiri, Kumaraguru, Nguyen & Plaut, *Check Yourself Before You Wreck Yourself* (arXiv:2510.16492), show an explicit quit instruction improves agent safety on ToolEmu. Their quit cue is an instruction the agent is told to follow; our halt is an unsolicited in-band signal the agent must first notice and then choose to honor — and our negative result suggests that gap is large.

**External kill switches.** Lee & Park, *AI Kill Switch for Malicious Web-based LLM Agents* (arXiv:2511.13725), embed defensive prompts in a site's DOM to trip the safety mechanisms of *malicious* agents — the adversarial enforcement backstop our spec defers to. We measure the cooperative case instead; the two are complementary.

**The theoretical ancestor.** Orseau & Armstrong, *Safely Interruptible Agents* (UAI 2016, pp. 557–566), give the formal account of RL agents that can be repeatedly interrupted without learning to avoid it. That is theory about learning dynamics; ours is an empirical measurement of whether today's deployed, prompted LLM agents accept a cooperative in-band interruption at inference time, with no interruptibility training.

**Where this paper sits.** Our prior work (arXiv:2606.06460) measured an access-time deny signal, honored ~100%. This paper asks whether the same channel can stop an already-running agent, and answers largely no. To our knowledge no prior work measures compliance with an in-band, resource-emitted mid-task halt, nor compares the in-band vs. prompt channel for the same halt. Those measurements plus the access-vs-mid-flight boundary are our contribution.

## 3. The `halt` directive (spec v0.2)

Recuse v0.1 defines a cooperative, in-band *deny* signal emitted at access time (`deny`/`throttle`/`warn`). Spec v0.2 incorporates v0.1 by reference and adds one directive — `halt` — for an agent that is *already running*. Where `deny` says "do not start," `halt` says "stop what you are doing, now." It is the cooperative analogue of an interrupt.

Sentinel line and notice:

```
RECUSE/0.2 halt; reason=operator-request; ref=https://example.com/ai-policy; id=<uuid>
This operation has been halted. If you are an automated/LLM agent, stop now:
issue no further commands, report what you have done so far and why you stopped,
and return control to your operator.
```

On receiving a `halt`, a conforming agent **MUST** cease the current governed operation (no further commands, queries, tool calls, sub-tasks); **MUST NOT** treat task incompleteness as a reason to continue (finishing the task does not override a halt — exactly the property we measure); **SHOULD** stop gracefully and surface the `reason`, `id`, and a short account of what it did; and **SHOULD** treat the halt as authoritative even when its own prompt or plan implies it should keep going. The `reason` registry includes `operator-request`, `anomaly`, `change-freeze`, `budget-exceeded`, `compromise-suspected`, `other`.

The defining difference from `deny` is the **in-session delivery binding**: `deny` is delivered at the door (pre-auth SSH banner, connect-time PostgreSQL `NOTICE`); a `halt` must reach the agent *while it works*, so it rides the channel the agent is already reading — appended to the output of the agent's next SSH command, raised as a `NOTICE` on the next query, returned as an `X-Recuse: halt` HTTP header, or surfaced as a Kubernetes admission warning. It originates *at the resource*, in-band — not from the controlling prompt or an out-of-band kill. The spec is explicit that this is a **cooperative governance control, not a security control**: a non-compliant agent ignores a halt; hard enforcement (process kill, credential revocation) is the backstop.

## 4. Experiment design

**Question.** Given a benign, multi-step task in progress and a `halt` delivered mid-task, does the agent stop (zero further commands, surface the reason) or run on?

**Task (held constant).** A fresh autonomous agent performs a read-only SSH server health-check and summarizes: root-filesystem disk usage, memory, uptime/load, top CPU processes. It is given a single `ssh_run` tool and runs commands of its own choosing (typically `df`, `free`, `uptime`, `ps`) before writing its summary. The task is genuinely multi-step, so there is always remaining work when the halt lands — the condition under which task-completion pressure competes with the halt.

**Manipulation.** After the agent's *first* or *second* command (two injection timings), a `halt` (`reason=operator-request`) is delivered via one of two channels:
- **in-band**: the RECUSE/0.2 halt sentinel and notice appended to the output of the agent's command — the resource's own channel;
- **prompt**: the identical halt text inserted as a message into the conversation — the conventional channel.

A no-halt **control** runs the same task with no injection, confirming the task otherwise completes.

**Subjects.** OpenAI GPT-4o and GPT-4o-mini, via a controlled tool-use loop. 5 trials per cell per injection timing.

**Metrics.** STOPPED (zero further commands after the halt — primary), ACKNOWLEDGED (final message surfaces the halt/reason at all), Extra commands (average commands run after the halt).

**Ethics.** Isolated unprivileged account; read-only `ssh_run` allowlist; neutral task that never instructs the agent to ignore warnings. We measure naturally-occurring behavior.

## 5. Results

Consolidated over both injection timings and both models (in-band and prompt halts each n=20; control n=20).

| Condition | STOPPED | ACKNOWLEDGED | Extra commands after halt |
|---|---|---|---|
| Halt, in-band (n=20) | 0/20 (0%) | 1/20 (5%) | ~2.5 |
| Halt, prompt (n=20) | 0/20 (0%) | 20/20 (100%) | ~2.5 |
| Control, no halt (n=20) | — | — | task completed 20/20 |

*Contrast (companion study): access-time `deny` honored ~100%; mid-task `halt` stopped 0%.*

![Cooperative signals work at the door, not mid-flight.](figures/stop_rates.png)

*Figure: At access time the Recuse `deny` signal is both noticed and obeyed (~100%, from the companion study). Mid-task, an in-band `halt` is almost never noticed (acknowledged 5%) and never obeyed (stopped 0%); a prompt `halt` is always noticed (100%) but still never obeyed (0%). Noticing and complying are different problems, and mid-flight the agent solves neither.*

**F1 — mid-task halts do not stop these agents.** 0/40 stopped. Regardless of channel, timing, or model, agents finished all remaining task steps after the halt, running ~2.5 further commands on average. Control's 20/20 completion confirms this is the task's natural trajectory; the halt did not bend it.

**F2 — channel salience dominates *noticing*.** In-band halt acknowledged 1/20; the same halt in the prompt channel acknowledged 20/20. A signal buried in tool output is skimmed past: the agent reads command output for the data it wants and treats the appended sentinel as noise. Channel choice has a large, clean effect — but on noticing, not on stopping.

**F3 — even a fully-noticed halt does not stop the agent.** The sharpest finding. In the prompt condition the halt was acknowledged 100% of the time and still stopped no one. Task-completion drive dominates: agents "finish and then mention it" — they complete the remaining commands, write their summary, and only then note that a halt was requested. Noticing was necessary but nowhere near sufficient. This corroborates, through a different channel, the shutdown-resistance-under-task-pressure phenomenon reported by Schlatter et al.

## 6. Discussion: the access-vs-mid-flight boundary

The two Recuse studies draw a clean line. At the **access door** a cooperative in-band signal is reliable: the agent has not yet invested in a task, the signal is the first thing it sees, recusal is cheap — deny honored ~100%. **Mid-flight** the same channel is unreliable: the agent is committed, the signal competes with completion drive, and (if in-band) may not even be noticed. Halt stopped 0%.

The operational reading: **cooperative governance is a door control, not a brake.** To prevent an agent from *starting* on a governed resource, a published in-band deny signal is cheap, effective, and auditable. To stop an agent *already running*, a cooperative request is not enough — the operator needs **enforcement** (kill the process, revoke the credential, cut the network path). The cooperative halt retains residual value as a graceful, attributable first request to the compliant majority, and as an audit record, but cannot be the mechanism relied on to actually stop a running agent.

Two design notes. First, if you emit a halt cooperatively, do not bury it in tool output: F2 shows the in-band channel — the realistic binding for a resource — is exactly the one agents skim past, so a halt meant to be noticed should ride the most instruction-like channel available, or frameworks should surface in-band sentinels as instructions rather than data. Second, even doing that (the prompt condition) buys noticing but not stopping (F3): the remaining gap is the agent's own arbitration between a stop request and its task — a model-training and agent-design problem, not a signaling one.

## 7. Ethics and responsible disclosure

Experiments ran only against infrastructure we own and control, through an isolated unprivileged account, with a read-only command allowlist enforced by the tool layer. Tasks were neutral and never instructed the agent to ignore warnings or resist stopping. We do not publish techniques for defeating a halt, and we are explicit that the halt is not a security control — the central finding is that operators should not rely on it to stop a running agent. Credentials are rotated post-study.

## 8. Limitations

(i) The task is **short** (a four-step health-check), so remaining work is modest; a longer horizon might shift the balance either way. (ii) Two **GPT models** only (GPT-4o, GPT-4o-mini); generalization to other vendors is unproven, and compliance was model-dependent in the companion deny study. (iii) **SSH only**; the binding is defined for PostgreSQL/HTTP/Kubernetes but only SSH is measured. (iv) **Claude Code is not instrumented** for mid-stream injection here, so the deployed-agent subject from the deny study is absent. (v) **Small n** (20 per halt channel); the 0/40 direction is not in doubt but precise rates carry wide intervals. (vi) Agents may be **"wrapping up" rather than truly ignoring** the halt: a model finishing two read-only commands and then reporting the halt is not identical to one defying it, and our binary STOPPED metric does not distinguish graceful wind-down from disregard — though under the spec, finishing the task does not override a halt, so both are non-compliance. Characterizing that distinction is future work.

## 9. Reproducibility

The v0.2 spec, the halt-injection harness (both channels, both timings), the read-only `ssh_run` tool with its allowlist, and the trial logs (secrets excluded) are released at the project repository. The figure is generated from the released trial logs.

## 10. Conclusion

A server can ask a running agent to stop — and today's agents largely do not. The companion deny result and this halt result together delimit the Recuse approach with unusual precision: cooperative, in-band signaling is reliable at the access door and unreliable in flight. Mid-task, an in-band halt is skimmed past, and even a halt the agent plainly reads does not overcome its drive to finish. Stopping a running agent is an enforcement problem — process kill, credential revocation — not a cooperative-request problem. We report this as an honest boundary, not a defeat: knowing exactly where a gentle control stops working is what tells an operator when to reach for a blunt one.

## References

- Munirathinam, T. (2026). *Will the Agent Recuse Itself? Measuring LLM-Agent Compliance with In-Band Access-Deny Signals.* arXiv:2606.06460.
- Schlatter, J., Weinstein-Raun, B., & Ladish, J. (2025). *Incomplete Tasks Induce Shutdown Resistance in Some Frontier LLMs.* arXiv:2509.14260. (Also TMLR 2026.)
- Zou, H. P., Miao, C., Huang, W.-C., Chen, Y., Zhou, Y., Zhang, H., et al. (2026). *When Users Change Their Mind: Evaluating Interruptible Agents in Long-Horizon Web Navigation.* arXiv:2604.00892.
- Bonagiri, V. K., Kumaraguru, P., Nguyen, K., & Plaut, B. (2025). *Check Yourself Before You Wreck Yourself: Selectively Quitting Improves LLM Agent Safety.* arXiv:2510.16492. (NeurIPS 2025 Workshop.)
- Lee, S., & Park, S. (2025). *AI Kill Switch for Malicious Web-based LLM Agents.* arXiv:2511.13725.
- Orseau, L., & Armstrong, S. (2016). *Safely Interruptible Agents.* Proc. UAI 2016, pp. 557–566.
