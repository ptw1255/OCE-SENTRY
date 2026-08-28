# Product portfolio: OCE Sentry

## Why this product exists

During an incident, the operator's scarce resource is attention. Queue facts, prior evidence, applicable investigations, service health, access readiness, and follow-up work may exist in different systems. OCE Sentry brings those decision inputs into a local terminal while preserving system-of-record and human-action boundaries.

## From why to what

1. [Product brief](product-brief.md)
2. [Users and JTBD](users-and-jtbd.md)
3. [Value proposition](value-proposition.md)
4. [Pain points and opportunity costs](pain-points-and-opportunity-costs.md)
5. [Wireframes](wireframes.md)
6. [Roadmap and success metrics](roadmap-and-success-metrics.md)

## Evidence discipline

- **Evidence:** verified in repository code, tests, or docs.
- **Inference:** a product interpretation of that evidence.
- **Hypothesis:** a testable belief requiring operator or usage evidence.
- **Assumption:** an unverified constraint.

### Evidence register

| Claim | Type | Source |
|---|---|---|
| The product provides a local terminal incident queue and headless mode. | Evidence | `oce_sentry/tui/app.py`; `oce_sentry/cli.py`; `README.md` |
| Failed refreshes retain last-known data and visibly mark it stale. | Evidence | `oce_sentry/tui/app.py` (`_apply_incidents`) |
| Executable investigations require confirmation and show resolved arguments/effects. | Evidence | `oce_sentry/tui/app.py` (`ConfirmRun`); `oce_sentry/actions.py` |
| Incident-derived values are passed as arguments rather than shell syntax. | Evidence | `oce_sentry/actions.py` (`build_command`, `run_action`) |
| The console includes separate views for service indicators, action kits/library, settings/access, payload composition, and issue tracking. | Evidence | `oce_sentry/tui/`; `oce_sentry/cli.py` |
| Faster operator orientation is the intended outcome. | Inference | Product structure and `README.md` |

No runtime incident records, personal data, internal endpoints, access-group names, adoption evidence, time savings, reliability outcomes, or business metrics appear in this portfolio.
