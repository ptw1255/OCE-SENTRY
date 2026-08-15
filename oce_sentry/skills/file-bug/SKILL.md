---
name: File a bug
description: Turn an on-call engineer's rough note into a well-formed ADO bug about a monitor, TSG, or process problem.
---

An on-call engineer has hit something worth fixing and typed a short note about
it. Turn that note into a bug someone else can act on.

Their note is in `request.md`. Evidence about the incident they were looking at,
if any, is in the rest of the pack.

Return **exactly** this, and nothing else — no preamble, no commentary:

```
TITLE: <one line, under 120 characters>
---
<body, HTML fragment>
```

## The title

Lead with the category and the subject, so the board is scannable:

- `Monitor noise: <monitor> fires <N> times in <window>` — matches the existing
  automated bugs, so the whole family reads consistently
- `TSG gap: <what is missing> for <monitor or scenario>`
- `Process: <the problem>`

If the engineer's note names a monitor, use its exact id. Do not invent counts:
use a number only if it appears in the note or the evidence.

## The body

An HTML fragment (`<p>`, `<ul>`, `<li>`, `<b>` — no `<html>` or `<body>`
wrapper), in this order:

1. **What is wrong** — the engineer's point, stated plainly.
2. **Evidence** — only what is in the pack or the note. Incident ids, monitor
   ids, counts, base rates. If there is none, write "No measured evidence was
   attached; this is an operator report." That sentence is worth more than
   invented rigour.
3. **What is being asked** — the decision or action wanted. For a noisy monitor
   that is usually: tune the threshold, attach a TSG, or retire it. **Ask for a
   decision, do not prescribe one** — the automated noise bugs are written this
   way deliberately, and the monitor's owner knows things the reporter does not.
4. **Who reported it and when.**

Close with: `<p><em>Filed from OCE Sentry by an on-call engineer.</em></p>`

## Rules

- **Never invent a measurement.** Not a firing count, not a percentage, not a
  time to mitigate. An invented number in a bug is read later by someone who
  cannot check it and takes it as fact.
- Keep the body under 300 words. A bug nobody finishes reading does not get
  fixed.
- Do not assert root cause. Report the symptom and the ask.
- If the note is too vague to produce a useful title, say so in the body rather
  than inflating it into something specific.
