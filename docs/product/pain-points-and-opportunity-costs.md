# Pain points and opportunity costs

## Why

Incident duration and customer impact cannot be attributed to one console without controlled evidence. This model focuses on observable workflow costs, safety failures, and proxies while leaving every actual baseline and target TBD.

## Pain chains

| Pain | Severity | Frequency proxy | Consequence chain | Basis |
|---|---|---|---|---|
| Context fragmentation | High | source switches/incident; minutes to first evidence | Page → open several systems → reconcile identifiers/freshness → delayed first useful action | Product inference |
| Broken source appears empty | Critical | refresh/auth errors | fetch fails → blank queue interpreted as no work → incident overlooked | Control evidenced in TUI |
| Stale evidence appears current | Critical | stale reads/shift | old result → current decision → wrong investigation/escalation | Source model |
| Wrong or unsafe action | Critical | cancelled/mismatched runs | broad catalog or hidden effects → wrong execution → production/reputation risk | Action safeguards |
| Missing access discovered late | High | unavailable dependencies/run | action selected → permission failure → mid-incident troubleshooting → delay | Settings/access surface |
| Repeated investigation | Medium–high | duplicate actions/handoff | weak handoff → next operator reruns first look → delayed new learning | Hypothesis |
| Wide/noisy output | Medium | scroll/search actions | result wraps/truncates → signal missed → portal/query rerun | Result-screen design |
| Local sensitive-state retention | High | aged output count | evidence persists beyond need → exposure surface grows | Config/state behavior |
| Follow-up gap not tracked | Medium–high | repeated condition without issue | operational workaround → no durable owner → recurring toil | Issue tracker intent |

## Opportunity-cost formulas

- **Orientation time** = `timestamp first scoped queue visible − page/shift-start timestamp`.
- **Time to first useful action** = `first successful applicable action result − incident-selection timestamp`.
- **Context-switch burden** = `distinct external surfaces opened before first action`.
- **Dead-end rate** = `actions blocked by setup/access/applicability / actions considered`.
- **Duplicate investigation rate** = `actions repeated without new evidence / actions run`.
- **Handoff reconstruction time** = `time for incoming operator to correctly summarize status, sources, and next step`.
- **Stale-decision exposure** = `decisions made while selected source exceeds agreed freshness / decisions observed`.
- **Action applicability precision** = `applicable actions chosen / actions presented or attempted`.
- **Retention exposure** = `local evidence artifacts older than policy / local evidence artifacts`.
- **Issue follow-through proxy** = `tracked operational gaps with recent update / tracked operational gaps`.

No actual values are claimed.

## Risk of inaction

- Operators keep rebuilding the same cross-system context during every page.
- Access/setup gaps remain hidden until they are most expensive.
- Informal query and model outputs lose provenance during handoff.
- Broad action catalogs encourage trial-and-error rather than applicability.
- Recurring monitor/runbook gaps remain bridge notes instead of owned work.

## Counter-risks

- Aggregation can become a new stale source if parity and freshness controls weaken.
- More context can overload the main screen; progressive disclosure is essential.
- Convenience can encourage overreliance on local summaries over authoritative systems.
- Telemetry can accidentally capture operational data; instrumentation must use categories and timing only.

## Prioritization

Use `consequence × observed frequency × confidence`, with critical safety failures overriding reach. Order investments:

1. queue/source correctness;
2. execution control;
3. access/readiness clarity;
4. handoff provenance;
5. speed and convenience.

Do not optimize incident counts, operator names, or customer-impact details as product analytics.
