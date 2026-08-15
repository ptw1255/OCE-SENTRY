# Skill sources

Where the actions in the library come from.

Sentry lists only skills ODSP owns in Azure DevOps. Nothing is copied into this
repository — a vendored copy of somebody else's skill forks the moment they
edit the original — and nothing is discovered from anywhere else.

Two sources were removed deliberately:

- **`~/.copilot/skills`**, the operator's personal Copilot CLI skills. Whatever
  someone happens to have installed for their own work is not an incident tool,
  and personal writing-voice skills were turning up beside mitigation skills.
- **Sentry's own bundled skills.** An OCE should run what the SRE team
  maintains and reviews, not a parallel set that exists only here and drifts
  from it.

One bundled skill survives, unlisted: `file-bug` is machinery behind the Create
Bug action rather than something you browse to, so it is loaded by id and never
appears in the library. See `load_internal_skill` in `oce_sentry/skills.py`.

Public GitHub skill repositories are **not** used. They were evaluated and none
ships an IcM, Geneva or SharePoint skill, so adopting one would have meant
maintaining a rewrite of someone else's prose against our own systems.

```powershell
$repos = "$HOME\repos"
[Environment]::SetEnvironmentVariable('OCE_SENTRY_SKILLS', (@(
  "$repos\ODSP-SRE-AI-Skills\skills",
  "$repos\SRELivesite-RCAAgent\.github\skills",
  "$repos\SRELivesite-RCAAgent\services\spo\sre\skills",
  "$repos\SRELivesite-RCAAgent\services\spo\meta\skills"
) -join ';'), 'User')
```

`OCE_SENTRY_SKILLS` takes a list separated by the platform path separator. The
useful skills live in several repositories at once, so supporting one directory
would force a choice between them.

Earlier entries win on id collision.

With nothing configured, Sentry finds no skills at all. That is intentional: an
empty library is a clear signal to go and clone the sources, whereas a fallback
set is a quiet invitation to use the wrong thing.

---

## Kits

A **kit** is a named, ordered set of these skills, run against one incident.
Kits are declared in `oce_sentry/policy/kits.json` and named for the question
they answer, because that is what an on-call engineer is holding when they open
the screen — not a skill name.

| Kit | Answers |
| --- | --- |
| First look | Is this real, and how big is it? |
| What changed | Did a deployment, flight, or code change cause this? |
| Alert storm | Many alerts fired at once. Is this one problem or many? |
| Infrastructure sweep | Is the platform underneath the service unhealthy? |
| Error hunt | The infrastructure is healthy, so what is throwing? |
| Customer impact | Who is affected, and what do we owe them? |
| MeTA media | Which media component is failing, and why? |
| Mitigate | What can I actually do to stop this now? |
| Close out | It is mitigated. Is the ticket good enough to close? |

Rules that keep the list honest, enforced by `tests/test_kits.py`:

- Every declared skill id must resolve on a configured machine. A kit naming a
  missing skill reports itself incomplete rather than running short — a
  playbook that silently skips a step produces an answer you will trust more
  than it deserves.
- Kits stay at four skills or fewer. Past that the operator stops reading the
  output, which is the same failure as not running it.
- **No kit contains a skill that writes** to IcM, ADO, or email. Writes stay
  deliberate and single. A batch run is the worst place to discover a side
  effect, because nobody is reading each step before it happens.
- Order is cheap-and-broad first, so a kit stopped halfway has still produced
  the useful part.

---

## Internal sources (Azure DevOps, `onedrive` org)

Both repositories require an Azure CLI token to clone; interactive git auth is
disabled:

```powershell
$tok = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv
git -c http.extraheader="Authorization: Bearer $tok" clone https://dev.azure.com/onedrive/SPARC/_git/ODSP-SRE-AI-Skills
git -c http.extraheader="Authorization: Bearer $tok" clone --depth 1 https://dev.azure.com/onedrive/SPARC/_git/SRELivesite-RCAAgent
```

### `ODSP-SRE-AI-Skills` — 22 skills

A dedicated SRE skills collection, and the highest-value source found. Skills
here are standalone and mostly incident-facing.

| Skill | What it does |
| --- | --- |
| `correlation-ai` | Decodes an SPO correlation ID to timestamp/farm/machine and generates Geneva DGrep links for ULS, RequestUsage and CustCorIdLookup. Also handles Graph request ids, Power Platform client ids and MeTA correlation vectors |
| `pr-detective` | Given a regression or an incident id, finds which recent PRs in SPO.Core and odsp-web could have caused it. Searches the last 8 weeks |
| `parse-stack` | Resolves a Watson crash stack to function names using PDB symbols |
| `icm-reliability-analysis` | Reliability analysis for an incident, or for a subscription over a window |
| `customer-common-rca` | Finds customers with recurring incidents, ranks by frequency, and produces a cross-incident common-cause report |
| `sla-credit-calculator` | Computes tenant downtime and the allowable SLA service-credit percentage from RequestUsage telemetry |
| `pir-author` / `pir-review` | Authors or reviews a customer-facing Post Incident Report, including the CRI-to-LSI privacy gate |
| `scrub-cri2lsi` | The sanctioned path from a Customer Reported Incident to a scrubbed LSI |
| `icm-tag` | Adds, updates, removes and lists IcM tags |
| `get-support-case` / `csam-lookup` / `csam-notify` | Support case and account-team lookups |
| `shd-post` / `send-aircover` | Service Health Dashboard and comms |

### `SRELivesite-RCAAgent` — the live site agent

Three directories worth adding, for different reasons.

**`.github/skills/` — 28 repo-level skills.** The RCA agent's own commands.
Notable: `investigate` (routes an incident to the owning team's expert),
`investigate-generic` (the fallback for un-onboarded teams), `triage-fleet`
(triages a surge of alerts at once), `impact` (quantitative impact analysis),
`geterrors` (Prod vs MSIT error-code growth), `livesite-management-hygiene`,
`log-work-item` / `resolve-work-item`, `save-report`.

**`services/spo/sre/skills/` — 20 central SRE skills.** The most reusable set,
because they are written for the SRE team rather than one service: `icm`
(central triage and component identification), `mitigation` (maps a root cause
to TSG mitigations), `outage-pattern` (detects a wider outage or a repeat
offender), `fcm` (flight and configuration change correlation), `ado-search`
(recent code and config changes), plus `sql`, `redis`, `dns`, `network`, `rps`,
`usr`, `spods`, `unexpected-error` and `communications`.

**`services/spo/meta/skills/` — 5 MeTA skills.** `investigate` plus the
component experts: `thumbnail`, `pdf`, `html`, `video-transcode`.

Other teams have their own folders under `services/spo/<team>/skills/` — gls,
observability, deployment, modernvideo, tps and more. Add whichever team you
carry the pager for.

---

## What is hidden, and why

Sentry hides skills that build or maintain the agent fleet rather than work an
incident: `generate-skill`, `onboard-team`, `discover-monitors`, `devbox`,
`launchOCEAgent`, `updateOCEAgentConfig`, `improve-tsg`, the `eval-*` harnesses
and similar. They are real skills and still reachable — press `a` in the library
to show them — but a console that lists "onboard a team" beside "assess blast
radius" has the same problem as a queue that shows everything.

The full list is `MAINTENANCE_SKILLS` in `oce_sentry/catalog.py`. It is a
denylist rather than an allowlist so a genuinely new incident skill appears
without anyone having to add it.


