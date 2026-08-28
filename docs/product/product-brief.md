# Product brief

## Why

An on-call operator must answer, in order: what is active, what is in scope, what is already known, what action is applicable, what the action will do, and whether supporting systems are trustworthy. Switching among systems increases cognitive load and makes stale, missing, or inaccessible evidence easier to misread as “nothing found.”

**Evidence:** the repository models source freshness/error, incident scope, actions, indicators, connector readiness, and local outputs across `oce_sentry/models.py`, `oce_sentry/sources/`, `oce_sentry/actions.py`, and `oce_sentry/tui/`.
**Inference:** the product's core value is decision-ready orientation with visible uncertainty, not automation for its own sake.

## Thesis

If an on-call operator can inspect scoped incidents, source freshness, existing evidence, and safe next actions in one keyboard-driven local console, then they can begin the right investigation with less context switching while retaining explicit control over every write or production query.

## What

A local terminal and headless workflow that:

- loads a policy-scoped active incident queue;
- presents high-signal incident detail and staleness;
- matches applicable deterministic query kits, skills, and reference links;
- composes an evidence handoff for an external agent;
- displays evaluated service indicators and error-budget context;
- reports connector/data-plane readiness and documented access guidance;
- tracks follow-up issues and gates creation behind preview;
- persists local action outputs with provenance; and
- degrades visibly when sources or optional integrations are unavailable.

## Scope

- Read-oriented triage, evidence discovery, and local investigation.
- Explicit confirmation for actions that execute against production data.
- Clear distinction among measured results, model answers, links, and unavailable data.
- Optional integrations that add capability without making the base queue silently incorrect.
- Headless parity for scripts and agents that cannot drive a TUI.

## Non-goals

- Replacing the incident or work-item systems of record.
- Autonomous mitigation, production-resource changes, or “run all” actions.
- Deriving authoritative metrics that upstream systems did not evaluate.
- Hiding missing access, missing sources, stale data, or partial action coverage.
- Persisting runtime incident content or credentials in the repository.
- Ranking individual operators.

## Principles

1. **Attention is the constrained resource.**
2. **Unavailable is not empty.**
3. **Stale is usable only when visibly stale.**
4. **Deterministic retrieval; bounded model judgment.**
5. **Nothing executes implicitly.**
6. **Show applicability, side effects, and provenance before action.**
7. **Local identity; no copied credentials.**
8. **System-of-record boundaries remain intact.**
9. **Headless and interactive paths should agree.**

## Risks and controls

| Risk | Repository control | Open product question |
|---|---|---|
| Wrong/stale queue | Policy required; source envelope; last-known data marked stale | How long should stale data remain visible? |
| Unsafe command construction | Argument-vector execution without shell in `actions.py` | How should optional skill execution communicate broader permissions? |
| Wrong action for incident | Applicability matching and explicit confirmation | How should multiple plausible actions be ranked? |
| Missing access looks like no evidence | Settings and readiness surfaces | Which remediation belongs in-context versus setup docs? |
| Model prose becomes “evidence” | Evidence-pack and action-result separation | How should citations be shown compactly in terminal width? |
| Local output accumulation | State/output pruning in TUI lifecycle | What retention window best balances handoff and privacy? |

## Evidence gaps

The repository does not establish operator adoption, reduced time to first useful action, fewer mistakes, lower incident duration, or issue-resolution outcomes. Those remain hypotheses.
