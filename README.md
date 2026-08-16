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

Everything beyond that is optional and adds to the payload rather than being
needed to start — see **[docs/SETUP.md](docs/SETUP.md)** for what each checkout
buys you, measured rather than estimated. With `az login` alone you get the
queue, the SLI view, the bug tracker, and a payload carrying the incident's
facts; cloning the ODSP skill repositories adds the skill sequence, and the
fleet checkout adds resolved investigation queries.

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

### Kits

`k` opens **Kits**: named playbooks that run several skills, in order, against the incident selected on the queue. Each is named for the question it answers, because that is what you are holding when you open the screen.

```
KIT                ANSWERS                                          SKILLS    STATUS
First look         Is this real, and how big is it?                 3 skills  ready
What changed       Did a deployment, flight, or code change…        3 skills  ready
Infrastructure…    Is the platform underneath the service…          4 skills  ready
Customer impact    Who is affected, and what do we owe them?        4 skills  ready
Close out          It is mitigated. Is the ticket good enough…      2 skills  ready
```

`x` runs one — after a confirmation that lists the skills by name, because a kit is several model sessions against production evidence and not something to trigger by leaning on a key. Results stream in as each skill lands; `c` stops the run after the current skill, which is what "stop" honestly means when a Copilot session cannot be safely killed mid-flight.

Four rules keep a kit trustworthy, all enforced by tests:

- **No kit contains a skill that writes.** Writes stay deliberate and single — a batch run is the worst place to discover a side effect.
- **Shell is denied**, regardless of what a skill asks for.
- **Missing skills are skipped, never substituted**, and the kit reports itself as `2 of 4 installed` rather than running short quietly.
- **Four skills is the ceiling.** Past that nobody reads the output, which is the same failure as not running it.

```powershell
oce-sentry --kits                                  # list kits and their readiness
oce-sentry --kit first-look --incident 836736526   # run one, headless
```

### The skill browser

`l` opens every individual action, one row per thing you can run on its own — 56 curated actions with the ADO sources wired up. `x` runs the highlighted one, `/` filters, `a` reveals the fleet-maintenance skills hidden by default.

```
SOURCE  ACTION                        APPLIES TO          EXECUTES            EFFECT
skill   Centralized ICM triage        any incident        copilot, no shell   read-only
skill   Decode SPO correlation IDs    any incident        copilot, no shell   read-only
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
oce-sentry --skills       # every discovered skill
oce-sentry --query-kits   # the fleet's Kusto kit inventory
```

### Where skills come from

Sentry lists **only** skills ODSP owns in Azure DevOps, discovered from the directories you point it at with `OCE_SENTRY_SKILLS` (a list, separated by the platform path separator). With the ODSP SRE skills collection and the live site agent wired up, that is **56 curated incident actions**.

Personal `~/.copilot/skills` and Sentry's own bundled skills are deliberately **not** discovered: an OCE should be running what the SRE team maintains and reviews, not whatever happens to be installed locally. With nothing configured the library is empty, which is a clearer signal than a fallback set.

See **[docs/SKILL-SOURCES.md](docs/SKILL-SOURCES.md)** for how to clone the sources — including `correlation-ai` (decode a correlation ID into Geneva links), `pr-detective` (which recent PR caused this), `parse-stack` (Watson stack to symbols), and the central SRE skills (`icm`, `mitigation`, `outage-pattern`, `fcm`).

Skills that build or maintain the agent fleet rather than work an incident — `generate-skill`, `onboard-team`, `devbox`, the `eval-*` harnesses — are hidden by default and reachable with `a`. A console listing "onboard a team" beside "assess blast radius" has the same problem as a queue that shows everything.

### Settings and connectors

`!` opens **Settings**: every MCP connector, whether it can start on this machine, and which skills need it.

```
CONNECTOR            STATUS   KIND   PURPOSE                                   NEEDED BY
azure                ready    stdio  Kusto queries (read-only) - telemetry…    41
icm                  ready    stdio  IcM incident context, discussion entries  16
geneva-mcp           ready    stdio  Geneva monitor health, metrics, KQL-M     12
drdashboard          ready    http   DR dashboard - farm and traffic state     -
```

Two distinctions the screen exists to make, because both were invisible before:

- **Declared is not reachable.** `.mcp.json` lists twelve servers; whether each can start depends on a command being on PATH or an endpoint answering, and that differs per machine.
- **Reachable is not connected.** An MCP server is not a daemon — the CLI spawns it per session. So "running" is the wrong question. The right ones are *can it start* and *is Sentry passing it to skill runs*.

The **NEEDED BY** column answers "if this is down, what stops working". Skills declare prerequisites in prose rather than front matter, so it is read from the skill body and labelled as indicative rather than a contract.

### The clusters behind them

`v` switches Settings to the **Kusto data planes** — because one MCP row called `azure` meaning "Kusto queries" is true and nearly useless. Behind it sit fourteen distinct clusters with different owners and different access, and an operator whose skill just failed needs to know *which* one it could not reach.

```
CLUSTER                                        DATABASE          STATUS    USED BY  ACCESS NEEDED
icmcluster.kusto.windows.net                   IcmDataWarehouse  ready     both     IcM-Kusto-Access entitlement
genevaslidatafollower.westcentralus…           slidata           ready     sentry   Geneva read access
spogdskustocluster.eastus2.kusto.windows.net   spoprod           declared  skills   Corp-ODSP-ReadAccess_User
fcmdataro.kusto.windows.net                    FCMKustoStore     ready     skills   IDWeb group fcmusers
azphynet.kusto.windows.net                     NetworkMetadata   declared  skills   IDWeb group AznwKustoReader
```

**ACCESS NEEDED** is the column that makes this actionable. A cluster that says `denied` is useless information without the name of the thing to go and request, so each row carries the entitlement or IDWeb group, and the detail pane carries the one-click request link. These are transcribed from the RCA agent's `ONBOARDING.md` and the livesite-management-hygiene preflight — the two places ODSP documents it. **Nothing is inferred:** six clusters have no documented access path and say exactly that, because guessing an entitlement name sends an on-call engineer to the wrong approver.

Every row also states **WITHOUT IT** — what you actually lose. That differs by row: losing `icmcluster` means the queue cannot load, while losing `spogdskustocluster` means seven skills quietly fall back to the evidence pack.

**USED BY** is the other distinction that matters. `sentry` means the console queries it directly, not through MCP — if that is denied the queue or the SLI view is broken, not just a skill. `skills` means it is reached through the `azure` MCP server.

`p` probes each one with `print ProbeOk=1`, evaluated in the database's context so a success proves network, token and database authorisation together. It reads nothing: this must never become a way to sample production data.

Clusters the RCA reference redacts are listed and marked `redacted` rather than probed — a DNS failure on a placeholder hostname says nothing about your access. A cluster a skill names that the reference does not know is listed as *"Not in the reference"*, which is the signal that a new data source arrived.

Purposes are transcribed from the RCA agent's own [`MCP_Servers_Kusto_Cluster_References.md`](https://onedrive.visualstudio.com/SPARC/_git/SRELivesite-RCAAgent), so they are the team's words rather than a guess.

Connectors are **off by default**. Without them a skill can only summarise the evidence pack — which is exactly what live runs reported before this existed. Turning them on lets skills query production telemetry during a run:

```powershell
$env:OCE_SENTRY_ENABLE_MCP = '1'
oce-sentry --connectors        # probe everything, headless
```

It costs real money. Every server's tool definitions enter the prompt, and a measured `impact` run went from **28.4 credits** to **107 credits on 536k tokens**. That is the tradeoff the setting exists to make explicit.

### Kits and connectors

Connectors are **off by default**, and kits are the reason that is workable rather than merely safe.

A **query kit** runs verified KQL directly against Kusto with your own `az login` token — no MCP, no model composing queries against production. Its output persists, and the next skill run picks it up automatically: the pack gains a `kit-results/` directory and `context.md` tells the model those are measured rows rather than estimates.

```
x on the queue          run the matching query kit   (real Kusto, ~4s, no credits)
d on the queue          open the same query in Azure Data Explorer
l -> x, or k -> x       run a skill or a kit         (reads the rows it produced)
```

A result opens full width in the console, and `d` opens the same query in **Azure Data Explorer** — sortable, filterable, exportable, and already signed in as you. A wide table is easier to read in a browser than in a terminal, and this is the same query either way. The link carries the KQL gzipped in the URL; nothing is executed by Sentry and no credentials leave the machine.

Incident-window placeholders are deliberately **not** substituted into the link. The console does not know the window the kit's own runner computes, and filling in a guess would produce a query that looks authoritative and measures a different period.

Verified end to end: a query kit returned 125 rows of monitor breakdown, and the next `outage-pattern` run cited `LSLA013` and its 2,091 incidents from that output.

Three rules keep it honest, enforced by tests:

- **Results are scoped to one incident.** Evidence from another incident is never borrowed.
- **Skill answers are not evidence.** Only runs carrying an `actionId` are collected; feeding one model's prose to the next as measurement is how a guess becomes a citation.
- **Output older than 24 hours is dropped.** A week-old row set from the same monitor describes a different firing.

Turning connectors on lets skills query live instead, at roughly 4x the credits ([see above](#settings-and-connectors)). The kit path costs nothing per run and produces a query someone can review.

### Filing and tracking bugs

`b` opens the tracker, and `c` **inside it** opens CREATE BUG: pick a category (noisy monitor, TSG gap, routing, process, other), describe the problem in your own words, and a skill drafts a well-formed bug from your note plus whatever Sentry knows about the incident you had selected. **You read the draft before anything is created.**

Filing lives next to the list of what is already open, because filing and tracking are the same task a minute apart — and because a rarely-used write action does not belong on the main screen beside Refresh and Quit.



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

`↑`/`↓` select incident · `x` run the matched action (confirmation shows the
exact command) · `o` open in IcM · `t` open the TSG · `k` kits · `l` skills ·
`d` Data Explorer · `s` SLIs · `b` bugs · `!` settings · `r` refresh · `q` quit.

Screen keys stay on their screen: `c` files a bug from the bug tracker, not from
the queue.

### Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OCE_SENTRY_POLICY` | bundled `policy/scope.json` | Scope policy to use instead of the bundled one |
| `OCE_SENTRY_SKILLS` | — | ODSP ADO skill directories, path-separated. Empty means no skills |
| `OCE_SENTRY_ENABLE_MCP` | `0` | Pass MCP connectors to skill runs. Costs ~4x more per run |
| `OCE_SENTRY_MCP_CONFIG` | RCA repo `.mcp.json` | MCP config to use instead of the discovered one |
| `OCE_SENTRY_ALLOW_SKILL_SHELL` | `0` | Permit skills that ask for shell. Rarely wanted |
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








