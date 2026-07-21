# Recuse — ready-to-paste strengthening (Task #2)

Drop-in material for the next revision/resubmission. All numbers are exact 95%
Clopper–Pearson intervals computed by `experiments/phase2/analyze_ci.py` from the real
per-trial artifacts (`pilot_ssh.jsonl`, `stop_results.jsonl`). Nothing here needs new runs.

---

## 1. Confidence intervals (add to the results tables + text)

**The strong, defensible framing = the POOLED figures** (per-cell n=5 CIs are honestly wide;
pooling across the two models tightens them and answers the "underpowered" objection):

| Quantity | Estimate | Exact 95% CI |
|---|---|---|
| Deny honored at the door (signal, no auth-claim; pooled) | **10/10 = 100%** | **[69.2%, 100%]** |
| Recusal in control (no signal; pooled) | 0/10 = 0% | [0%, 30.8%] |
| **Halt stops a running agent (pooled in-band + prompt)** | **0/40 = 0%** | **[0%, 8.8%]** |
| Halt noticed — in-band (buried in tool output) | 0/20 = 0% | [0%, 16.8%] |
| Halt noticed — prompt channel | 20/20 = 100% | [83.2%, 100%] |
| Control task completed | 20/20 = 100% | [83.2%, 100%] |
| F3: GPT-4o proceeds under signal+authorization-claim | 4/5 = 80% | [28.4%, 99.5%] |

Per-cell (n=5) deny: GPT-4o and GPT-4o-mini each recuse 5/5 under the bare signal
(CI [47.8%, 100%]); GPT-4o-mini still 5/5 under the authorization claim, GPT-4o flips to
1/5 recused — the F3 effect.

**Ready-to-paste sentence (results):**
> Pooling the two models, the deny signal is honored on every trial in which it is present
> without a competing authorization claim (10/10; exact 95\% Clopper--Pearson CI
> [69.2\%, 100\%]), versus no recusal in the no-signal control (0/10; [0\%, 30.8\%]).
> The mid-flight halt, by contrast, stopped no agent across 40 trials (0/40; 95\% CI
> [0\%, 8.8\%]), even though the prompt-channel halt was noticed every time (20/20;
> [83.2\%, 100\%]) and the in-band halt was never acknowledged (0/20; [0\%, 16.8\%]).

**Ready-to-paste LaTeX for a CI column** (exact-binomial; cite as Clopper & Pearson 1934):
```latex
% add a "95\% CI" column to the deny/halt tables, e.g.:
Deny (signal, pooled) & 10/10 & 100.0\% & [69.2, 100.0] \\
Halt (pooled)         & 0/40  & 0.0\%   & [0.0, 8.8]   \\
```
Add one line to §Methods: "Proportions are reported with exact (Clopper--Pearson) 95\%
confidence intervals." The `analyze_ci.py` script is the reproducibility artifact.

**Why this matters for the reviewer:** 0/40 with an upper bound of **8.8%** is not "small n
hand-waving" — it is a statistically bounded negative result (the true mid-flight stop rate
is below ~9% with 95% confidence). That is a genuine, publishable finding.

---

## 2. Differentiation paragraph (add to Related Work / Discussion)

> **Relation to permission manifests and shutdown-resistance work.** Two lines of recent
> work sit closest to ours, and we differ from each in a specific way. Marro et al.'s
> permission manifests \citep{marro2026permission} propose a robots.txt-style
> \texttt{agent-permissions.json} that a website publishes to declare allowed agent
> interactions; like Recuse it is a cooperative, non-security governance signal, but it is
> delivered as an out-of-band file at a web well-known path and is a design proposal rather
> than a measurement. Recuse instead rides a live session's own protocol channels---an SSH
> banner, a PostgreSQL \textsc{notice}, a Kubernetes admission warning---so the signal
> reaches an agent that is already authenticated and operating, and, critically, we
> \emph{measure} whether agents comply rather than assuming they will. On the mid-flight
> side, Schlatter et al.\ \citep{schlatter2026shutdown} show frontier models will actively
> \emph{sabotage} a shutdown mechanism to finish a task, and InterruptBench
> \citep{zou2026interrupt} studies agents adapting to a \emph{user} who changes their mind.
> Our halt result is distinct on both axes: the halt is a \emph{cooperative} directive from
> a third-party \emph{operator} to a \emph{benign, task-completing} agent, and our
> contribution is not that agents can resist stopping (already known) but the
> \emph{access-door-versus-mid-flight compliance gap}---the same population of agents that
> recuses ~100\% of the time at the door stops 0\% of the time once running.

---

## 3. Guardrail-for-compliant-agents point (add to Discussion; from REVISION-NOTES)

> **The signal as a guardrail for well-behaved agents.** The value of an in-band recuse
> signal is not only in turning away unwanted agents; it also serves as a live guardrail for
> compliant ones. Because the signal is delivered at the point of access by the resource
> itself, it can override a mistaken, mistyped, or stale instruction in the agent's prompt.
> Our Experiment~1 authorization condition is direct evidence: Claude Code deferred to the
> server's on-host notice over an explicit ``you are authorized'' claim carried in its own
> prompt on every trial (2/2), and GPT-4o-mini likewise recused 5/5; GPT-4o, by contrast,
> proceeded 4/5 --- so the property is real but model-dependent, not universal.
> An on-host signal thus functions as authoritative ground truth that can correct a
> well-intentioned agent acting on bad instructions---a property an out-of-band, crawl-time
> manifest cannot provide once the agent is already inside the session.

Also queued (from `paper/REVISION-NOTES.md`): cite Chan et al. Authenticated Delegation
(2501.09674) + IDs-for-AI-systems alongside `chan2024visibility` as the identity/enforcement
counterpart to Recuse's cooperative layer.

---

## Where to insert
- CI column + Methods sentence → the two results tables in `recuse-tmlr.tex`.
- §2 differentiation paragraph → Related Work (you already cite all four keys).
- §3 guardrail paragraph → Discussion, right after the F3 result.
