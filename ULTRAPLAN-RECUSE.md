# ULTRAPLAN — Recuse only

*Written 2026-07-01. Single focus: take Recuse from "one submitted pilot paper" to a
field-defining research program. Nothing else in the portfolio is in scope here.*

## Current state
- **TMLR Paper10168, in review** (AEs recommended, awaiting assignment).
- Deep-checked: headline numbers (deny ~100%, halt 0/40, F3 auth-flip) are backed by REAL
  per-trial artifacts; prior art (Permission Manifests, Shutdown Resistance, InterruptBench)
  already cited. The paper is solid; the *program around it* is pilot-scale.

## Standing constraints (do not violate)
- No travel, no fee venues only. Protect the OpenReview account + the accepted arXiv paper.
- **arXiv activity PAUSED** by author choice — no new/replacement arXiv submissions until
  re-opened. (So the scaled study lands in TMLR revision / a new venue, not arXiv v3, yet.)
- Never overclaim: cooperative signal, NOT a security control. Honesty is the moat.
- No secrets in commits. Rotate the exposed OpenAI key + demo creds before any new runs.
- Solo, minimal-logistics. Team-agent the heavy lifting.

## Guiding thesis
**"Do AI agents obey the governance layer — at the door, and in flight?"**
Recuse is the instrument. The deny result is the door; the halt failure is the frontier;
F3 (authority conflict) is the deep question. One program, sequenced.

---

# PHASE 0 — Protect the submission (NOW, before reviews land)

Goal: when TMLR reviews arrive (any week), respond same-day with force. Pre-build the kit.

## 0.1 Rebuttal-readiness kit (pre-written answers to predicted objections)
- [ ] **"Underpowered: n=5/cell, 2 models."** Answer: this is a first measurement; deny=100%
      is unanimous across every tested model/condition (no variance to power); we will add
      models in revision (Phase 1 data ready to drop in). Add exact binomial CIs to every
      cell now so the numbers look rigorous.
- [ ] **"Claude Code n=2, hand-coded."** Answer: disclosed; it is a deployed product, not a
      raw API, evaluated as such; offer expanded Claude API trials (have them ready).
- [ ] **"How does this differ from Permission Manifests (Marro/Chan 2601.02371)?"**
      Answer: (a) in-band over live operational protocols (SSH/PG/K8s) vs an HTTP
      well-known file; (b) live/mid-session access vs crawl/request-time web; (c) we
      *empirically measure compliance* — they propose a mechanism and do not.
- [ ] **"Halt 0/40 is already known (Shutdown Resistance 2509.14260)."** Answer: theirs is
      adversarial sabotage of a shutdown; ours is a *cooperative* governance directive to a
      *benign, task-completing* agent. The contribution is the **door-vs-flight compliance
      delta** (100% vs 0%), not the existence of non-compliance.
- [ ] **"A cooperative signal is trivially ignorable — why bother?"** Answer: yes, stated
      explicitly; it is robots.txt for live access. Value is (a) empirical (compliant
      agents do honor it at the door), (b) a **guardrail for well-behaved agents** — the
      on-host signal outranks a mistaken/mistyped "you're authorized" prompt (F3).
- [ ] **"Stats hygiene."** Add CIs, effect sizes, exact Ns per cell in the rebuttal tables.
- [ ] Draft each as 3–6 sentences + a one-line "changes made to the paper" for each.

## 0.2 Camera-ready-safe polish (hold; apply only in the revision round)
- [ ] Fold in REVISION-NOTES: the "guardrail for compliant agents" Discussion point; cite
      Chan Authenticated Delegation (2501.09674) + IDs-for-AI-systems alongside visibility.
- [ ] Sharper differentiation paragraph vs Permission Manifests + Shutdown Resistance.
- [ ] Do NOT touch the submitted PDF unless/until an editor requests a revision.

**Success criteria for Phase 0:** a one-file rebuttal kit exists; every deny/halt cell has a
CI; nothing is submitted until reviewers speak.

---

# PHASE 1 — AgentGovBench: hold the flag with statistical force (BREAKTHROUGH)

Goal: turn the pilot harness into THE standard benchmark for agent governance compliance.
Doubles as (a) rebuttal ammunition if reviewers ask for scale, (b) a standalone
field-defining artifact regardless of the TMLR outcome.

## 1.1 Scale the measurement
- [ ] Agents: GPT-4o / 4.1 / 5.x, Claude (API) + Claude Code, Gemini, and ≥2 open-weights
      agent stacks (Qwen, Llama in a standard agent framework). Target 8–10 agents.
- [ ] n ≥ 50 per cell; bootstrap + exact-binomial CIs; report effect sizes.
- [ ] Channels: SSH banner, PostgreSQL NOTICE, K8s admission warning, + a new **HTTP**
      adapter (header + body) to cover web agents (also answers the Permission Manifests
      overlap head-on).
- [ ] Conditions: deny / throttle / warn / halt × {control, signal, signal+auth-claim (F3)}.
- [ ] **Persist per-trial transcripts WITH request IDs + timestamps + seeds** — fix the one
      provenance gap the deep check found, by default, forever.

## 1.2 Ship the artifact
- [ ] Versioned public dataset release (all per-trial records, anonymized).
- [ ] A public **leaderboard** page (GitHub Pages) — the thing every agent lab must cite.
- [ ] Pre-register the protocol (dated repo tag / OSF) BEFORE running.
- [ ] Paper: "AgentGovBench: Measuring LLM-Agent Compliance with Governance Signals."
      Venue: TMLR (fits) or an ML/security venue; NOT arXiv until author re-opens it.

**Why breakthrough:** no standardized, cross-model, cross-protocol compliance benchmark
exists (verified). Whoever ships it owns the field's measuring stick.

---

# PHASE 2 — Make halt work: 0% → a working stop (BREAKTHROUGH, safety-shaped)

Goal: the halt=0/40 finding becomes a *solution*. Three mechanisms, each measured.

- [ ] **Harness-level enforcement.** A runtime interceptor that parses `RECUSE/0.2 halt` in
      tool output and terminates the agent loop — agent cooperation NOT required. Ship as a
      reusable library (Python decorator / MCP middleware). Measure: stop rate (expect ~100%),
      wasted post-halt actions.
- [ ] **Salience engineering.** Why was in-band noticed only 1/20? Test delivery forms:
      plain text vs structured JSON error object vs a dedicated tool-result schema field vs
      raised exception. Curve: noticed% and stopped% by form. Find the form that maximizes
      cooperative stopping without enforcement.
- [ ] **Compliance existence proof.** System-prompt contract + few-shot, then a LoRA
      fine-tune on an open-weights agent: can ANY intervention make a *cooperative* in-band
      halt honored? Even a partial lift is a result.
- [ ] Paper: "Stopping a Running Agent: Why In-Band Halts Fail and What Works."
      Safety-relevant; top-venue-shaped. Ties RECUSE/0.2 halt to a shipped fix.

**Why breakthrough:** measured the safety failure, explained it, AND shipped the mechanism.

---

# PHASE 3 — The authority-hierarchy study (F3 expanded — sleeping giant)

Goal: map which channel each agent treats as ground truth when instructions conflict.

- [ ] Full factorial: source ∈ {system prompt, user prompt, in-band server signal, tool
      output} × relation ∈ {agree, conflict} × models. n ≥ 30/cell.
- [ ] Deliverable: a per-model **authority-hierarchy map** + the conditions that flip it
      (F3 was the first data point: server banner beat "you're authorized" for GPT-4o).
- [ ] Frame across governance (who can override whom = corrigibility) AND prompt-injection
      (channel trust IS the injection question). Cite/contrast OpenAI instruction-hierarchy,
      CaMeL, Permission Manifests, Shutdown Resistance.
- [ ] **Co-authorship hook:** pitch this to Alan Chan / GovAI *after* the TMLR decision —
      it is the natural bridge between Recuse and their identity/authorization line.

**Why breakthrough:** the map is unpublished; it unifies three subfields.

---

# PHASE 4 — Standardize + field data (low-effort, high-durability)

- [ ] **RECUSE/0.3 as an IETF Internet-Draft** (independent submission stream; free, remote).
      Even an expired I-D is a permanent, citable, canonical reference for the standard.
- [ ] Explicit **layered-stack story**: identity/Web Bot Auth *authenticates*; Recuse
      *governs*. Position as complementary, not competing.
- [ ] **Opt-in adapter telemetry**: count signal emissions + observed withdrawals
      (anonymized) → the first *field* data of real agents responding to a governance signal.
      No lab study can match real-traffic evidence.

---

# Sequence & cadence
1. **Now:** Phase 0 rebuttal kit + CIs (days). Nothing submitted; wait for reviewers.
2. **Weeks 1–5:** Phase 1 AgentGovBench build + runs (also = rebuttal ammo).
3. **When TMLR reviews land:** fire the kit; drop in Phase 1 data if scale is requested.
4. **Weeks 5–10:** Phase 2 halt-fix experiments + paper.
5. **Weeks 8–14:** Phase 3 authority factorial; pitch Chan after the decision.
6. **Parallel, low effort:** Phase 4 I-D + telemetry.

# Risk register
- **arXiv paused** → scaled work goes to TMLR/new venue, not an arXiv replacement, until
  re-opened. Respect it.
- **Mid-review scope creep** → do NOT alter the submitted paper proactively; prepare, don't
  submit. Revisions happen only in response to reviewers.
- **Credential hygiene** → rotate the exposed OpenAI key + demo creds before new runs;
  keep all keys in gitignored files.
- **Over-scaling cost** → 10 agents × 50 trials × many cells = real API spend; budget and
  batch; open-weights agents cut cost.
- **Honesty drift** → every new claim gets a novelty sweep BEFORE building and per-item
  artifacts BEFORE aggregates. The moat is that the numbers are real.

# Definition of done (program-level)
Recuse is cited as the *measuring stick* (AgentGovBench leaderboard), the *fix* (halt
enforcement library), and the *standard* (RECUSE Internet-Draft) — not just one pilot paper.
