# Value proposition

## Why

The product is not valuable because it puts many tools in a terminal. It is valuable when it reduces context switching while preserving the meaning, freshness, applicability, and authority of each input.

## Value proposition canvas

### Customer profile

| Jobs | Pains | Gains |
|---|---|---|
| Triage active incidents | Fragmented queue/evidence; time pressure | One scoped view with freshness |
| Choose an investigation | Runbook discovery and applicability uncertainty | Ranked, applicable actions with effects |
| Interpret health | Percentages without objective/budget context | Evaluated indicators and budget framing |
| Resolve setup/access gaps | Failure appears only at execution | Readiness and documented remediation |
| Handoff work | Links and prose lose provenance | Source-aware local evidence package |
| Track recurring gaps | Follow-up is separate from triage | Issue view and previewed creation |

### Value map

| Capability | Pain reliever | Gain creator |
|---|---|---|
| Scoped queue with source envelope | Avoids silent empty/freshness ambiguity | Faster orientation |
| Incident detail and action matching | Reduces portal/runbook search | Next action in context |
| Exact confirmation with effects | Reduces accidental execution | Operator trust and control |
| Deterministic query kits | Avoids composing production queries ad hoc | Reviewable, reusable measurement |
| Evidence composition | Separates measured artifacts from model judgment | Better handoff continuity |
| Settings/access views | Makes missing capability actionable | Earlier readiness repair |
| Headless parity | Supports scripts and external agents | Same model beyond TUI |

## Alternatives

| Alternative | Strength | Tradeoff |
|---|---|---|
| Open each authoritative portal | Full source fidelity | High switching and reconstruction cost |
| Team runbook/wiki | Reviewed guidance | Not automatically scoped to current incident |
| General AI assistant with live tools | Flexible reasoning | Higher cost/variance; provenance and side effects need discipline |
| Personal scripts/query history | Fast for expert author | Hard to discover, review, and hand off |
| Incident chat/bridge summary | Shared context | Can mix facts, decisions, and stale interpretation |

## Differentiation

1. **Visible degradation:** source failures remain errors, and last-known data is marked stale (`SourceResult`, `_apply_incidents`).
2. **Pre-action clarity:** confirmation shows the exact resolved argument vector and declared effects (`ConfirmRun`).
3. **Applicability-aware actions:** actions are matched rather than presented as universally safe (`actions_for`).
4. **Evidence separation:** persisted deterministic outputs are distinguishable from model answers (manifest/pack/action modules).
5. **Local-first security posture:** ambient identity and local state avoid copied credentials (`docs/SETUP.md`, auth/config modules).
6. **Interactive/headless parity:** key workflows are available through `oce_sentry/tui/` and `oce_sentry/cli.py`.

## Proof and limits

**Evidence:** tests cover actions, sources, scope, payloads, kits, and related boundaries under `tests/`.
**Evidence:** implementation retains stale data after refresh errors, avoids implicit shell use for deterministic actions, and persists result metadata.
**Limit:** repository implementation and tests do not prove adoption, lower operator effort, faster mitigation, or reduced incident impact.

## Assumptions

- Operators already have approved access and understand source-system responsibilities.
- The local machine is an acceptable place for time-bounded operational state.
- Upstream identifiers and metadata are sufficient for action matching.
- Team-maintained skills/runbooks remain reviewed and current.

## Hypotheses

- Freshness and provenance cues reduce false “no issue/no evidence” conclusions.
- Exact pre-run effects increase action confidence without materially delaying triage.
- Showing unavailable capabilities with remediation reduces mid-incident dead ends.
- A question-oriented kit library outperforms a flat tool catalog for first-look work.
- Structured handoffs reduce repeated investigations across shift changes.

## Positioning

For on-call engineers handling complex live-site work, OCE Sentry is a local terminal decision surface that combines scoped incident context, evidence freshness, applicable investigations, and readiness guidance. Unlike a portal collection or general agent, it makes uncertainty and side effects explicit and keeps mitigation authority outside the console.
