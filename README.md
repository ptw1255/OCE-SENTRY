# OCE Sentry

**The incident console for an on-call engineer.** It puts the queue, the
evidence already gathered, the runbooks and investigation kits that apply, and
the known-noise backlog into one local app on your machine.

> Status: **working v1**. The live incident queue, detail pane, kit execution,
> and headless mode all run today. The remaining work is polish and deeper
> product surfacing.

## What it does

OCE Sentry is built for the moment an incident pages and you need answers in
this order:

1. Is this in scope, and has anything already looked at it?
2. Have we seen this exact condition before, and what happened last time?
3. What should I run first?
4. Is this monitor a known noise source with a bug already filed?

It assembles those answers from the evidence already on hand, so you do not
have to stitch them together while the page is live.

## Product shape

- Queue-first incident view
- Read-only by default
- Deterministic fetch; the model only judges evidence that was already gathered
- Hash-gated caching so a new judgment only happens when the evidence changes
- Explicit writes only, with gated actions and explanatory comments
- Actionable rows only, with hidden counts in the status line

## What it is not

- Not a second incident system of record. IcM remains authoritative.
- Not a mitigation tool. It does not restart, scale, or change production resources.
- Not a new pipeline. It computes nothing that is not already in the evidence.

## Start here

```powershell
python -m pip install "git+https://github.com/parkerwall_microsoft/oce-sentry.git@main"

az login
oce-sentry              # the TUI
oce-sentry --once       # one fetch, console dump, exit
```

Prerequisites: Python 3.10+ and Azure CLI signed in.

Everything beyond that is optional and adds to the payload rather than being
needed to start.

## How it works

Modelled on the `risk-management-harness` (Risk Sentry) in
`onedrive/Security/appsec-ai-tools`, and its sibling PR Sentry. The patterns
carried over deliberately:

- Tabbed Textual TUI, `python -m oce_sentry`, `--once` for a console dump, `--dev` for hot reload.
- `az login` is the entire auth story. No PATs, no token files, no secrets in config.
- Deterministic fetch; the model judges only.
- Hash-gated caching.
- Nearly read-only.
- Only actionable rows are shown.

### Optional extras

| Want | Set |
| --- | --- |
| Investigation kits and runbooks | `OCE_SENTRY_KITS` |
| Alternate scope policy | `OCE_SENTRY_POLICY` |
| Shared fleet checkout | `OCE_SENTRY_FLEET_REPO` |
| Tracking history | `OCE_SENTRY_WATCHLIST` |

All read-only, all optional. PowerShell 7 (`pwsh`) is needed only to run a kit;
without it the queue works and runbook execution reports the missing
dependency.

### Service level indicators

```powershell
oce-sentry --slis              # trailing 24h
oce-sentry --slis --hours 168  # trailing 7d
```

In the TUI, `s` opens the SLI view, `w` cycles the window, `e` and `g` switch
the breakdown, and `r` refreshes.

### Kits

`k` opens Kits: named playbooks that run several skills, in order, against the
incident selected on the queue. Each is named for the question it answers,
because that is what you are holding when you open the screen.

`x` runs one after confirmation, and `c` stops the run after the current skill.

### Skills

`l` opens every individual action, one row per thing you can run on its own.
`/` filters, and `a` reveals the maintenance skills hidden by default.

### Settings

`!` opens Settings: every connector, whether it can start on this machine, and
which skills need it.

## Docs

- [docs/DATA-MAP.md](docs/DATA-MAP.md)
- [docs/RUNBOOK-SOURCES.md](docs/RUNBOOK-SOURCES.md)
- [docs/SKILL-SOURCES.md](docs/SKILL-SOURCES.md)
- [docs/SETUP.md](docs/SETUP.md)

