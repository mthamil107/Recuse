# Paper revision notes

Things to fold into the **next substantive revision** of `recuse-paper.tex` (e.g., the
scaled study or a workshop version). Batch these — avoid churny single-day arXiv
replacements.

> **Status (2026-06-24): arXiv activity PAUSED by author choice** (after AMP +
> prompt-injection were rejected, to let the account cool off). Do not submit/replace
> anything on arXiv until the author re-opens this.

---

## TODO #0 (PRIMARY) — merge the stop-signal study into a v3 of arXiv:2606.06460

arXiv moderators **declined the standalone stop paper** (`submit/7743026`) as too
overlapping with 2606.06460, and **explicitly invited** folding it into a replacement of
that paper (no privilege warning — a sanctioned path). When arXiv activity resumes, build
**v3 of `recuse-paper.tex`** that incorporates the halt/stop work:

- Source material is ready in `paper-stop/` (recuse-stop.tex/.md, figure `stop_rates.png`)
  and `spec/recuse-signal-v0.2.md` (the `halt` directive).
- Fold in: the `halt` directive (RECUSE/0.2), the stop experiment + results
  (in-band 0/20 stopped & 1/20 acknowledged; prompt 0/20 & 20/20; control 20/20), the
  boundary finding (cooperative signals work at the access door ~100% but NOT mid-flight,
  0%), and the new verified citations (Schlatter shutdown-resistance 2509.14260,
  InterruptBench 2604.00892, Bonagiri 2510.16492, Lee&Park 2511.13725, Orseau&Armstrong
  UAI 2016) — already in `paper-stop/references.bib`.
- Title decision pending: keep "...In-Band Access-Deny Signals" vs lightly broaden to
  "...In-Band Access Signals" / "...Governance Signals" to cover deny + halt.
- Honesty: keep the deny=100% vs halt=0% boundary framing; do not overclaim.

## Discussion (§7): the signal as a guardrail for *compliant* agents

Add 1–2 sentences in the Discussion, building on the existing finding that the in-band
policy can outrank prompt authorization (F3 — Claude Code trusting the server's banner
over a "you're authorized" claim in its prompt).

**Point to make:** the value of the signal isn't only stopping *unwanted* agents — it
also acts as a **guardrail for well-behaved ones**. It delivers a live, authoritative
"this is production" reminder *at the point of access*, which can override a **mistaken,
mistyped, or otherwise wrong user prompt**, or an agent that has **drifted off task**.
The pilot already supports this: agents honored the server's on-host notice over an
explicit "authorized" claim in their prompt. So the on-host signal functions as
ground-truth context that corrects a compliant agent acting on bad instructions.

*Origin:* sharpened during email correspondence with Alan Chan (GovAI), June 2026 — the
substance is in the data (F3), but this clean "guardrail for the well-behaved" framing
was not yet written into the paper.

---

## Related work / citations to add

Cite Alan Chan's authorization-and-identity line of work — it is the *enforcement/
identity* counterpart to Recuse's *cooperative signal*, and supports the discussion
point that "identification enables enforcement; recusal is the cooperative layer on
top." Verify full bib fields (authors, arXiv ID, venue) at add-time:

- **"Authenticated Delegation and Authorized AI Agents"** (2025) — most relevant; how an
  agent proves it is authorized. Pairs directly with our "on-host policy vs. the prompt's
  authorization claim" finding (F3).
- **"IDs for AI Systems"** (Chan et al., 2024) — agent identifiers; the "IDs that enable
  blocking" Alan raised. Cite alongside `chan2024visibility`.
- Optional framing anchor: **"Open Problems in Technical AI Governance"** (2024) to situate
  Recuse within the technical-governance agenda.

<!-- Add further revision notes below as they come up (scaled study, more models,
signal variants, multi-rater judge coding, etc.). -->
