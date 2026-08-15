---
name: Handover note
description: What the next on-call engineer needs to know about this incident.
---

Write the handover note for this incident: what the next person coming on
shift needs, and nothing they do not.

Cover:

- **State.** Where this stands right now, in one or two sentences.
- **What has already been done.** Read `kit-results/` if present — those are
  investigations the current engineer already ran, and repeating them is the
  most common waste at a shift change.
- **What is still open.** The specific unanswered question, not a general area.
- **Why it is still open.** Waiting on a team, needs a repro, low priority
  because the base rate says it self-heals — whatever the evidence supports.

Rules:

- If the pack shows the fleet has examined this many times without material
  change, say so. "This has been open 28 days and looked at 25 times without
  changing" is often the single most useful sentence in the note.
- Do not repeat the incident title or restate fields the reader can see in IcM.
- Every claim traceable to the evidence. No speculation about cause.
- Under 150 words. This is read at shift change by someone with six other
  things open.
- Output only the note.
