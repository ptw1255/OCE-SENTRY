# OCE Sentry

**A terminal an On-Call Engineer can work an incident from.** Active incidents,
the evidence already gathered about them, the runbooks and investigation kits
that apply, and the noise bugs behind the pages — in one place, on your own
machine, without hosting anything.

> Status: **working v1**. The live incident queue, the detail pane, kit
> execution and headless mode all run today. Tabs for ADO noise bugs and fleet
> health are not built yet.

---

## Who this is for

You are on call. An incident pages. You want, in this order:

1. **Is this in scope, and has anything already looked at it?**
2. **Have we seen this exact condition before, and what happened last time?**
3. **What should I run first?**
4. **Is this monitor a known noise source with a bug already filed?**

Every one of those questions already has an answer somewhere. The problem is
that the answers are spread across a SharePoint library, an IcM queue, an ADO
board, a Kusto cluster and a git repository — so in practice nobody assembles
them while a page is live. This console assembles them.

## What it is not

- **Not a second incident system of record.** IcM remains authoritative.
- **Not a mitigation tool.** It does not restart, scale, or change any
  production resource. That boundary is inherited from the fleet it reads and
  is not configurable.
- **Not a new pipeline.** It computes nothing. If a number is not already in
  the evidence, the console says so rather than deriving it — because a number
  derived here would eventually disagree with the report a stakeholder was
  sent.

## Where the data comes from

The console is a reader. Everything it shows is produced by the **MeTA live
site agent fleet**, which watches ODSP live site incidents, measures blast
radius, publishes reports, and files monitor-noise bugs.

See **[docs/DATA-MAP.md](docs/DATA-MAP.md)** for the full connection map:
every source, what it carries, how it is reached, how it authenticates, and how
stale it is allowed to get.

Short version:

| Question | Answered from |
| --- | --- |
| What is active and in scope? | Watchlist queue |
| What did we already learn? | Incident report + scope verdict, in the MeTA-SRE-Comms library |
| What do I run? | Investigation kits and runbooks — see [docs/RUNBOOK-SOURCES.md](docs/RUNBOOK-SOURCES.md) |
| Is this monitor known-noisy? | ADO noise bugs filed by the fleet |
| Can I trust what I'm looking at? | Fleet health — when each loop last ran |

## Experience model

Modelled on the `risk-management-harness` (Risk Sentry) in
`onedrive/Security/appsec-ai-tools`, and its sibling PR Sentry. The patterns
carried over deliberately:

- **Tabbed Textual TUI**, `python -m oce_sentry`, `--once` for a console dump,
  `--dev` for hot reload.
- **`az login` is the entire auth story.** No PATs, no token files, no secrets
  in config.
- **Deterministic fetch; the model judges only.** The harness gathers, and any
  AI layer reads only what was gathered. It cannot query, and it cannot produce
  a number.
- **Hash-gated AI caching** — re-judge when the underlying evidence changes,
  never on a timer.
- **Nearly read-only.** Writes are individually gated, off by default, applied
  only by an explicit click, and every one carries an explanatory comment.
- **Only actionable rows are shown**, with hidden counts in the status line. A
  flat list of everything tracked is not a work queue.

## Install

```powershell
python -m pip install "git+https://github.com/parkerwall_microsoft/oce-sentry.git@main"

az login
oce-sentry              # the TUI
oce-sentry --once       # one fetch, console dump, exit
```

That is the whole thing. Prerequisites: Python 3.10+ and Azure CLI signed in.

**Sentry is independent of the goobers pipeline.** It needs no daemon, no
instance directory, no pipeline checkout, and nothing the fleet publishes. The
queue is a live IcM query, and the scope policy that shapes it ships inside the
package at `oce_sentry/policy/scope.json`.

That policy was seeded from the MeTA fleet's `data-paths.json` so the two agree
on day one, and it records what it was seeded from. `--once` prints the
effective policy and its hash on every run, so a copy that has fallen behind is
visible rather than assumed.

### Optional extras

| Want | Set |
| --- | --- |
| Investigation kits / runbooks | `OCE_SENTRY_KITS` → a `kits/` directory |
| Track the fleet's scope instead of Sentry's own | `OCE_SENTRY_POLICY` → a `data-paths.json` |
| Both, from one fleet checkout | `OCE_SENTRY_FLEET_REPO` → the repo root |
| The fleet's tracking history ("looked at 25 times") | `OCE_SENTRY_WATCHLIST` → `watchlist.json` |

All read-only, all optional. PowerShell 7 (`pwsh`) is needed only to *run* a
kit; without it the queue works and runbook execution reports the missing
dependency.

### Service level indicators

```powershell
oce-sentry --slis              # trailing 24h
oce-sentry --slis --hours 168  # trailing 7d
```

In the TUI, `s` opens the SLI view: `w` cycles the window (1h → 6h → 24h → 3d → 7d → 30d), `e` and `g` switch the breakdown between environment and region, `r` refreshes.

Two SLIs are registered today — **Analysis Reliability** and **Web Reliability** — read from the Geneva SLI data plane (`genevaslidatafollower.westcentralus` / `slidata`). Sentry reads the windows Geneva already evaluated; it does not recompute reliability from raw telemetry, because a recomputed number would disagree with the one the SLO is actually measured on.

The headline is the **error budget**, not the percentage. An SLI reading 99.89% against a 99.9% objective sounds healthy and is in fact 107% of budget spent — that misreading is what this view exists to prevent.

Adding a third SLI is a config change, not a release: point `OCE_SENTRY_SLI_REGISTRY` at a JSON file of `{id, name, table, objective, description}` entries.

### The action library

`k` opens every action an OCE can run, in one list, bound to the incident selected on the queue. `x` runs the highlighted one.

```
SOURCE  ACTION                        APPLIES TO          EXECUTES            EFFECT
skill   Assess blast radius           any incident        copilot, no shell   read-only
skill   Draft the IcM enrichment      any incident        copilot, no shell   read-only
kusto   The Analysis Module QoS is…   AnalysisModuleQos   kusto query, local  writes
link    Open the TSG for this…        incident with TSG   opens in a browser  read-only
```

Three kinds of thing, deliberately in one place:

- **skills** — reason over the incident's evidence through Copilot. Most apply to any incident.
- **kusto kits** — a query verified against the live schema, plus 90 days of precomputed base rates for that specific condition. These only apply where the monitor matches.
- **links** — the TSG, when IcM recorded one.

Every row states what it applies to, what it will execute, and what it changes, before you run it. Actions that do not apply to the selected incident stay listed but greyed: offering something that cannot run is worse than showing it unavailable.

For a kusto kit, **the verdict leads** — the conclusion its base-rate card reached, not the statistics behind it:

> 3 of 84 firings were customer impacting. This is not noise. Do not wait for auto-mitigation.

That sentence is usually the whole answer, and it costs no query to read.

```powershell
oce-sentry --kits    # the same library, headless
```

### Filing and tracking bugs

`c` opens **CREATE BUG**: pick a category (noisy monitor, TSG gap, routing, process, other), describe the problem in your own words, and a skill drafts a well-formed bug from your note plus whatever Sentry knows about the incident on screen. **You read the draft before anything is created.** `b` opens the tracker.

```powershell
oce-sentry --bugs                      # open bugs, most-stalled first
oce-sentry --bugs --all                # include closed
oce-sentry --create-bug "the TSG links to a dashboard that no longer exists" \
           --category tsg --incident 836736526 --dry-run
```

Bugs are created in `onedrive/OneBranch`, area path `OneBranch\NEXUS\MeTA`, assigned to `parkerwall@microsoft.com`, tagged `meta-monitor-noise` — the same board and tag the fleet's automated noise bugs use, so one query finds them all. An extra `oce-sentry` tag keeps operator-filed bugs distinguishable within that set, and the tracker shows the difference in its SOURCE column.

The tracker sorts by **idle time**, not age: a bug filed months ago and touched yesterday is being worked; one filed last week and untouched since is not. Anything untouched for more than 14 days is called out.

Filing is the console's only write. It requires an explicit action, shows the exact work item first, and `--dry-run` prints the payload without creating anything.

### Headless

```powershell
python -m oce_sentry --once --limit 20
python -m oce_sentry --incident 836736526                 # detail + matching runbooks
python -m oce_sentry --incident 836736526 --run <kit-id>  # execute one
```

`--once` exists so this is usable from a script, a pipeline, or an agent that
cannot drive a TUI.

### Keys

`↑`/`↓` select incident · `[` / `]` choose action · `x` run the selected action
(confirmation shows the exact command) · `o` open in IcM · `t` open the TSG ·
`r` refresh · `q` quit.

### Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OCE_SENTRY_POLICY` | bundled `policy/scope.json` | Scope policy to use instead of the bundled one |
| `OCE_SENTRY_KITS` | — | Runbook directory. Absent means no actions |
| `OCE_SENTRY_WATCHLIST` | — | Fleet tracking history, enrichment only |
| `OCE_SENTRY_FLEET_REPO` | — | Convenience: policy + kits + watchlist from one checkout |
| `OCE_SENTRY_LOOKBACK_DAYS` | `30` | Incident creation window. Matches the fleet's collector |
| `OCE_SENTRY_INCIDENTS_INTERVAL` | `300` | Auto-refresh seconds |
| `OCE_SENTRY_QUERY_TIMEOUT` | `120` | Kusto timeout, seconds |
| `OCE_SENTRY_ACTION_TIMEOUT` | `900` | Runbook timeout, seconds |
| `OCE_SENTRY_STATE_DIR` | `%LOCALAPPDATA%\oce-sentry` | Cache and logs |
| `OCE_SENTRY_OUTPUT_DIR` | `<state>\output` | Runbook results. Refused if inside any git repo |

There is **no built-in scope fallback**. If the policy cannot be read the
console refuses to start, because a console silently running a stale copy of the
fleet's scope would show a confidently wrong queue.

## Verification

```powershell
python -m pytest                                  # 30 offline tests
$env:OCE_SENTRY_LIVE = "1"; python -m pytest      # + live parity against the fleet
```

The parity test is the one that matters: it compares **exact incident ids and
track reasons** against the fleet's own watchlist state, not counts — a
different set of the same size is not parity. At the time of writing the console
and the fleet agree on all 32 open in-scope incidents with zero `trackReason`
mismatches.

That agreement is by construction, not by coincidence: the KQL is built from the
fleet's `data-paths.json` using the same team list, severity branches,
environment classifier and `case()` precedence as `collect-watchlist.ps1`.

## Repository visibility

Created private because internal visibility requires an org-owned repository.
It belongs in `odsp-microsoft` alongside the other ODSP engineering tools, so
that ODSP engineers can find it; transferring preserves history and issues.

Content rules regardless of visibility:

- **No incident data is ever committed.** Incident IDs, titles, owner aliases
  and evidence are read at runtime and cached locally, never checked in.
- **No credentials, ever.** The ambient Azure identity is the only credential.
- Internal endpoints (cluster URIs, library paths, ADO org names) live in
  configuration, not in code, so the repository stays portable and reviewable.







