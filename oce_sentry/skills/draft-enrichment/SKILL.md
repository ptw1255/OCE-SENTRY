---
name: Draft the IcM enrichment
description: A comment the on-call engineer can review and paste into the incident.
---

Draft a comment for this incident that an on-call engineer would be willing to
post without editing it first.

Structure it as:

- **What we know.** The measured facts from the evidence, in plain sentences.
  No bullet soup, no restating the title.
- **What we do not know.** The specific gaps. This section is not optional; an
  enrichment that implies certainty it does not have is worse than none.
- **Suggested next step.** One, occasionally two. Say who would do it if it is
  not the on-call engineer.

Rules:

- **You are drafting, not posting.** Nothing here reaches IcM. Write it so a
  human reads it, decides, and pastes it themselves.
- Every figure must come from the evidence pack. An enrichment carrying an
  invented number is the single worst output this tool could produce — it will
  be read by people who were not in the investigation and cannot check it.
- Do not assert a root cause. If the evidence supports a hypothesis, label it a
  hypothesis and say what would confirm it.
- Write for someone who has just been paged and has not read anything else
  about this incident.
- Keep it under 200 words. An enrichment nobody finishes reading has failed.
- Output only the draft comment. No commentary about the drafting.
