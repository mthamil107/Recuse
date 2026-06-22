# Paper revision notes

Things to fold into the **next substantive revision** of `recuse-paper.tex` (e.g., the
scaled study or a workshop version). Batch these — avoid churny single-day arXiv
replacements.

---

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

<!-- Add further revision notes below as they come up (scaled study, more models,
signal variants, multi-rater judge coding, etc.). -->
