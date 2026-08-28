# Users and jobs to be done

## Why

Incident tooling must reflect distinct responsibilities: the current operator decides and acts, a handoff recipient reconstructs state, and service/tool maintainers improve coverage. Mixing these jobs can overload the main queue or create unsafe automation.

## Personas

| Persona | Type | Goal | Constraint |
|---|---|---|---|
| Primary on-call operator | Primary | Orient quickly, choose a safe next action, preserve evidence | Time pressure, incomplete context, production access |
| Incoming/handoff operator | Secondary | Reconstruct what was known, run, and still blocked | Needs provenance and freshness, not a transcript dump |
| Service/monitor owner | Secondary | Understand recurring/noisy conditions and follow-up work | Not necessarily active during the page |
| Tool/skill maintainer | Secondary | Keep action coverage, access guidance, and source mappings correct | Must not destabilize live triage |
| Autonomous mitigation bot operator | Negative | Let the console change production without confirmation | Conflicts with product boundary |
| Broad analytics consumer | Negative | Use the local queue as authoritative reporting or personnel data | Console is operational decision support, not reporting system |

**Inference:** personas are derived from workflow ownership in `README.md`, `oce_sentry/tui/`, and action/payload code; no user-research sample is present.

## Contexts and triggers

- A new page arrives and scope/impact are not yet clear.
- An operator takes over an existing incident.
- A monitor condition has a known deterministic investigation.
- A source refresh fails while the operator still needs last-known context.
- A skill or data source is unavailable because setup/access is incomplete.
- Investigation results must be handed to another operator or agent.
- A recurring tooling/monitoring problem needs follow-up tracking.

## Jobs

**Functional**

- Identify active, in-scope work and its urgency.
- Distinguish current, stale, missing, and failed evidence.
- Find the smallest applicable investigation and understand its effects.
- Run one action deliberately, preserve output, and continue from the result.
- Compose a portable, source-aware handoff.
- See which optional capability is unavailable and why.

**Emotional**

- Reduce uncertainty without being given false certainty.
- Trust that a keypress will not create an implicit production change.
- Avoid the panic of a blank queue caused by broken authentication.

**Social**

- Handoff concisely and defensibly.
- Show why a decision followed the available evidence.
- Respect team-owned runbooks and systems of record.

## JTBD statements

1. **When** I receive a page, **I want to** see scoped incidents with age, severity, flags, and source freshness, **so I can** choose what needs attention without opening several systems.
2. **When** a data source fails, **I want to** retain visibly stale last-known context and see the actual failure, **so I can** continue cautiously instead of misreading an empty view.
3. **When** an incident matches an investigation, **I want to** see applicability, side effects, and the exact local command before execution, **so I can** authorize the right action knowingly.
4. **When** no investigation matches, **I want to** know whether the cause is missing metadata, missing installation, missing access, or true lack of coverage, **so I can** choose the correct remedy.
5. **When** I hand off an incident, **I want to** compose a provenance-bearing evidence package, **so the next operator or agent can** continue without treating prior model prose as measured fact.
6. **When** a recurring operational gap needs durable follow-up, **I want to** preview a structured issue beside existing work, **so I can** avoid duplicates and understand the only intended write.

## User stories

- As an operator, I can refresh without losing my selected incident.
- As an operator, I can cancel before any action runs.
- As an operator, I can distinguish “no output” from “failed.”
- As an operator, I can use headless commands when a TUI is unavailable.
- As a handoff recipient, I can trace outputs to action, incident context, and time.
- As a maintainer, I can add an indicator via configuration rather than a release where supported.
- As a keyboard or screen-reader user, I can reach all critical actions without relying on color or pointer interaction.

## Forces of progress

| Push | Pull | Anxiety | Habit/inertia |
|---|---|---|---|
| Context scattered across operational systems | One local high-signal console | Aggregation may be stale or wrong | Opening familiar portals manually |
| Repeated first-look investigations | Applicable deterministic kits | Query may affect production or use wrong scope | Copying prior queries |
| Handoffs lose provenance | Evidence package and persisted output | Sensitive runtime data could spread | Chat summaries and links |
| Access failures appear late | Readiness and remediation view | Setup surface may be complex | Trying a command and troubleshooting reactively |

## Journeys

### New page

Open console → authenticate with ambient identity → inspect freshness and scope → select incident → review detail and applicable action → confirm exact effects → run → inspect/persist result → compose handoff or continue.

### Degraded source

Refresh → source error → last-known queue retained and marked stale → operator decides whether age is acceptable → opens readiness/remediation → retries or switches to authoritative system.

### Follow-up issue

Open issue tracker → search existing open work → choose category → enter operator-authored problem → review generated draft → explicitly create or cancel. Repository behavior for tracking/creation lives under `oce_sentry/bugs.py` and `oce_sentry/tui/bug_*`.
