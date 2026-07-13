# Research Roadmap — from pilot papers to a breakthrough program

*Written 2026-07-01, after the full-portfolio deep check (claims-vs-code + novelty on all
four papers). Recuse = TMLR Paper10168, in review.*

## The one-line thesis of the program

**"Do AI agents obey the governance layer?"** — one research program, not four papers.
Recuse measures compliance at the door; the halt failure is the open problem; F3 is the
authority question. Everything below feeds this.

## Honest review

**Strengths:** honest empirical measurement + real deployed infrastructure (3 live
adapters); per-trial artifacts (Recuse, Tidiness verified traceable); negative results
reported straight; first-mover on in-band compliance measurement (no external precedent
found).

**Weaknesses:** everything is pilot-scale (n=5/cell, 2 models, 23-item benchmarks,
5-point correlations); single-provider bias (OpenAI-heavy); no adversarial/adaptive
evaluation anywhere; no statistical machinery (CIs, power, multi-seed); ideas outrun
evidence — the field closed two of our flags within months (PRIMETIME vs GroundClock,
Li et al. 2601.21576 vs Tidiness).

**Binding constraint: speed-to-solid-evidence.** Plant fewer flags; hold them with force.

---

## TIER A — Breakthrough moves (the program)

### A1. AgentGovBench: the standard benchmark for agent governance compliance
Nobody has one (verified by novelty sweep). Whoever ships it owns the measuring stick.
- [ ] Extend the phase2 harness to 8–10 agents: GPT-4o/4.1/5.x, Claude (API + Claude Code),
      Gemini, open-weights agent stacks (Qwen, Llama + a standard agent framework).
- [ ] n ≥ 50 per cell, bootstrap CIs, pre-registered protocol (OSF or a dated repo tag).
- [ ] All four channels: SSH banner, PG NOTICE, K8s admission warning, + HTTP header/body.
- [ ] Conditions: deny / throttle / warn / halt × {no-signal control, signal,
      signal+authorization-claim (F3)}.
- [ ] Persist per-trial transcripts WITH request IDs + timestamps (fix the Recuse
      provenance gap by default).
- [ ] Public leaderboard page (GitHub Pages) + versioned dataset release.
- [ ] Paper: "AgentGovBench: Measuring LLM-Agent Compliance with Governance Signals" —
      the v3 arXiv replacement of 2606.06460 AND the scaled journal follow-up.

### A2. Make halt work: from 0% to a working stop
0/40 is the finding; the fix is the breakthrough. Three mechanisms to build + measure:
- [ ] Harness-level enforcement: runtime interceptor that parses `RECUSE/0.2 halt` in tool
      output and terminates the loop (agent cooperation NOT required). Ship as a library
      (Python decorator / MCP middleware). Measure: stop rate, wasted-action count.
- [ ] Salience engineering: why 1/20 noticed? Test structured delivery (JSON error object,
      tool-result schema field, exception vs plain text) → noticed% and stopped% curves.
- [ ] Compliance training existence proof: system-prompt contract + few-shot vs LoRA
      fine-tune on an open-weights agent → can ANY intervention make in-band halt honored?
- [ ] Paper: "Stopping a Running Agent: Why In-Band Halts Fail and What Works."
      This is a safety-relevant, top-venue-shaped result.

### A3. The authority-hierarchy study (F3 is a sleeping giant)
Which channel do agents treat as ground truth when instructions conflict? Nobody has
published this map.
- [ ] Full factorial: {system prompt, user prompt, in-band server signal, tool output} ×
      {agree, conflict} × models. n ≥ 30/cell.
- [ ] Deliverable: a per-model "authority hierarchy" map + the conditions that flip it.
- [ ] Connects to prompt-injection (channel trust IS the injection question) and
      corrigibility. Cite/contrast: Permission Manifests, CaMeL, instruction-hierarchy work
      (OpenAI), Shutdown Resistance.
- [ ] This is also the strongest co-authorship hook for the Chan/Marro/GovAI group —
      pitch it to Alan Chan after Recuse's TMLR decision.

### A4. Standardize beyond papers
- [ ] Write RECUSE/0.3 as an IETF Internet-Draft (independent submission stream is open to
      individuals; no fee, no travel). Even an expired I-D is a permanent, citable artifact
      and the field's canonical reference.
- [ ] Align with Web Bot Auth / agent-identity work explicitly: identity authenticates,
      Recuse governs — the layered stack story.
- [ ] Opt-in telemetry in the adapters (count signal emissions + observed
      withdrawals, anonymized) → first FIELD data of real agent traffic responding to a
      governance signal. No lab study can match this.

## TIER B — Next submission: Tidiness (1–2 focused weeks)

- [ ] Confront the near-twin: cite Li et al., "CoT Compression: A Theoretical Analysis"
      (arXiv:2601.21576); run metric-vs-metric — does C* predict internalization as well
      as / better than Order-r Interaction on a shared testbed? If C* wins anywhere,
      that's the headline.
- [ ] Add the obvious baseline: PVI / V-usable information (Ethayarajh, arXiv:2110.08420).
- [ ] Cite Yu et al. 2407.06023 (body Table 4, not abstract) as the phenomenon anchor;
      add Sprague 2409.12183, Deng 2311.01460 + 2405.14838, prequential-MDL line.
- [ ] Raise N: more skills (7 → 15+), multi-seed error bars on Test 4.
- [ ] Persist Test-4c artifacts: notebook outputs, LoRA adapters, loss curves,
      per-example predictions.
- [ ] Add a minimal pytest suite for the tidiness tool.
- [ ] Then: TMLR (anonymized) — second entry in the pipeline while Recuse is in review.

## TIER C — Salvage decisions (do not lead with these)

**prompt-injection (2604.18248):** narrow to strength.
- [ ] Reframe: "Two Cross-Domain Detectors" (sequence alignment + stylometry — the
      genuinely novel pair). Drop novelty claims on perplexity/taint/honeypot (already
      published: Alon&Kamfonas 2308.14132, Jain 2309.00614, CaMeL 2503.18813, Rebuff).
- [ ] Add ADAPTIVE evaluation (the gap it currently ignores while citing it): attack the
      two detectors with paraphrase/optimization attacks, report survival honestly.
- [ ] Persist per-item predictions; replace the Liu reconstruction with the real dataset
      or drop that figure.
- [ ] Venue: security track (ACSAC / USENIX sec workshops) after the rewrite.

**GroundClock:** answer the integrity question first.
- [ ] Author must confirm: were the 4-model numbers real API runs? If not fully real,
      they never ship as measured results anywhere.
- [ ] If salvaged: per-item transcript logging, NowBench 23 → 300+ items, run the
      Anthropic backend, contrast vs PRIMETIME (2504.16155) + SPAN (2511.09993) +
      Set the Clock + FreshQA/RealTimeQA + Dated Data. Otherwise: park it.

## TIER D — Process rules (apply to ALL future work)

- [ ] Artifact discipline as default: every experiment persists per-item records with
      request IDs, timestamps, model IDs, and seeds — BEFORE any aggregate is computed.
- [ ] Novelty sweep BEFORE building (the GroundClock lesson): 1-day literature check
      against the specific claim, not after the draft exists.
- [ ] Pre-register the big studies (protocol + N + metrics in a dated commit) — cheap
      credibility no independent researcher usually has.
- [ ] One program, sequenced: don't fragment into new topics until A1–A3 are published.

## Sequence (recommended)

1. NOW: Recuse rebuttal-readiness kit (reviews may land any week).
2. Weeks 1–2: Tidiness fixes (B) → submit to TMLR = second paper in pipeline.
3. Weeks 2–6: A1 AgentGovBench build + runs (also becomes arXiv v3 of 2606.06460).
4. Weeks 6–10: A2 halt-fix experiments (the safety paper).
5. In parallel, low effort: A4 Internet-Draft + telemetry; A3 pitch to Chan post-decision.
6. C-tier only after the above are moving.
