# Skill sources

Where the actions in the library come from, and which are worth wiring up.

Sentry ships four skills. Everything else is discovered from directories you
point it at, and nothing is ever copied into this repository — a vendored copy
of somebody else's skill forks the moment they edit the original.

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

Earlier entries win on id collision, so your own copy of a skill beats a shared
one, and a shared one beats the copy Sentry ships.

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

---

## Public sources worth adapting

None of these ship an IcM, Geneva or SharePoint skill — those have to be
written — but the structure and prose are sound.

| Repository | Licence | Why |
| --- | --- | --- |
| [`microsoft/azure-skills`](https://github.com/microsoft/azure-skills) | MIT | Microsoft's official Copilot skill plugin. `azure-kusto` is the closest thing to a drop-in KQL authoring skill; `azure-diagnostics` and `azure-reliability` are close in shape to blast-radius work. References Azure MCP tools that Sentry denies, so the prose survives and the tool calls do not |
| [`github/awesome-copilot`](https://github.com/github/awesome-copilot) | MIT | The canonical `SKILL.md` format spec and catalogue. `docs/README.skills.md` is the authoritative description of the format Sentry parses |
| [`cocallaw/KQL-ADX-Expert`](https://github.com/cocallaw/KQL-ADX-Expert) | MIT | The deepest public KQL skill: operator reference, annotated patterns, per-service table routing |
| [`selvarajmurugesan90/ops-engineering-skills`](https://github.com/selvarajmurugesan90/ops-engineering-skills) | Apache-2.0 | 296 ops skills. The `site-reliability-engineering` domain has blameless postmortem, incident response and on-call, SLO/SLI and error-budget design |
| [`lukemurraynz/SREAgentSkill`](https://github.com/lukemurraynz/SREAgentSkill) | unstated | Not for its content but for its safety model: an explicit L0–L4 autonomy ladder, stop conditions, and "start read-only when ownership is unclear". Close to Sentry's own deny-shell default, and worth reading before widening any permission |

A licence that is not stated is not a licence. Read before copying.
