# Roadmap and success metrics

## Why

Success means the operator reaches trustworthy evidence and an appropriate next action with less friction—without weakening source correctness, action control, privacy, or authority boundaries.

## Phased roadmap

| Phase | Outcome | Candidate work | Exit evidence |
|---|---|---|---|
| 0 — Workflow baseline | Understand orientation, dead ends, and handoffs | Privacy-safe task study; synthetic error drills; freshness comprehension | Operators correctly distinguish empty, stale, and unavailable |
| 1 — State clarity | Every source/action state has an unambiguous response | Empty/error/remediation copy; no-row success; timeout/partial treatment; narrow-terminal details | Seeded failures produce correct operator decisions |
| 2 — Action selection | Smallest applicable action is easier to choose | Question-first ranking; applicability reasons; prerequisite preview; recent-result indicator | Lower dead-end and duplicate-action proxies |
| 3 — Handoff continuity | Incoming operator can resume from provenance | Compact activity/evidence timeline; retention controls; handoff completeness check | Faster correct reconstruction in simulation |
| 4 — Readiness before page | Setup/access gaps surface outside incidents | Headless readiness summary; drift detection; maintainer ownership links | Fewer mid-incident prerequisite failures |
| 5 — Governed learning | Product improves from safe aggregate evidence | Opt-in event aggregation; review cadence; runbook coverage feedback | Guardrails hold and decisions use observed patterns |

Roadmap items are proposals, not commitments.

## Hypotheses

1. Operators act more accurately when empty, unavailable, and stale states are visually distinct.
2. Showing applicability rationale and prerequisites reduces failed action starts.
3. Question-oriented kits shorten selection compared with a flat skill list.
4. A provenance-first handoff reduces repeated first-look investigations.
5. Readiness checks run before an on-call shift reduce access failures during incidents.
6. Full-width result views reduce missed signal compared with wrapped side-pane output.

## Metrics

| Type | Metric | Definition |
|---|---|---|
| Leading | Queue comprehension | scenario questions answered correctly for fresh/empty/stale/error states |
| Leading | Applicable-action selection | first selected action valid for current context / action-selection attempts |
| Leading | Prerequisite readiness | required configured dependencies ready at shift/start check / required dependencies |
| Leading | Handoff completeness | handoffs containing freshness, actions, results, blockers, and next step / handoffs |
| Leading | Explicit-result classification | runs classified correctly as success/no rows/failure/timeout |
| Lagging | Orientation time | first trusted scoped view − start/page time |
| Lagging | Time to first useful evidence | first useful deterministic result − incident selection |
| Lagging | Handoff reconstruction time | time for incoming operator to correctly state current situation and next step |
| Lagging | Duplicate investigation proxy | repeated actions without changed evidence / actions |
| Guardrail | Silent source failure | source failures rendered as successful empty state; target zero |
| Guardrail | Unconfirmed execution | actions started without explicit confirmation; target zero |
| Guardrail | Unauthorized production changes | production-resource changes initiated by console; target zero |
| Guardrail | Runtime content in telemetry | incident/evidence/identity/endpoint content captured; target zero |
| Guardrail | Headless/TUI scope mismatch | differing scoped record identifiers under same policy/time; target zero |

No baseline or achieved target is claimed.

## Instrumentation

Proposed local, opt-in events with categorical properties only:

| Event | Allowed properties |
|---|---|
| `queue_fetch` | success, duration bucket, result-count bucket, freshness bucket, error category |
| `incident_selected` | age bucket, severity class, applicable-action-count bucket |
| `action_previewed` | action kind, applicable boolean, effect class, prerequisite state |
| `action_decision` | run/cancel, action kind |
| `action_result` | success/no-rows/failure/timeout, duration bucket, row-count bucket |
| `handoff_composed` | section-presence flags, source-count bucket, age bucket |
| `readiness_checked` | ready/declared/denied/unknown counts, no names/endpoints |
| `view_resized` | width bucket, detail mode |

Never collect runtime identifiers, titles, summaries, owners, commands, query text, output, file paths, credentials, endpoint names, issue content, or access-group names.

## Experiments

| Experiment | Comparison | Success signal | Guardrail |
|---|---|---|---|
| State comprehension drill | Current status vs explicit empty/stale/unavailable panels | Higher correct scenario decisions | No slower recognition of active work |
| Action discovery | Flat catalog vs question-first applicable set | Faster correct first choice | No reduction in discoverable alternatives |
| Prerequisite preview | Failure-at-run vs pre-run readiness | Fewer failed starts | Preview does not expose sensitive configuration |
| Handoff design | Free-form note vs structured provenance summary | Faster correct reconstruction | No extra runtime content retained |
| Result layout | Side-pane wrap vs dedicated full-width view | Higher answer accuracy on synthetic wide output | Keyboard return remains obvious |

## Governance

- Review guardrails before speed metrics.
- Use synthetic or redacted scenarios for studies.
- Treat incident mitigation time and customer impact as contextual outcomes, not product-caused metrics.
- Sunset instrumentation that cannot justify its privacy cost.
