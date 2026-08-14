# OCE Sentry

**A terminal an On-Call Engineer can work an incident from.** Active incidents,
the evidence already gathered about them, the runbooks and investigation kits
that apply, and the noise bugs behind the pages — in one place, on your own
machine, without hosting anything.

> Status: **design**. Nothing is implemented yet. This repository currently
> holds the data map, the runbook-source contract, and the issues that describe
> the build.

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

## Planned install

```powershell
git clone https://github.com/parkerwall_microsoft/oce-sentry.git
cd oce-sentry
python -m pip install -e .

az login
python -m oce_sentry          # the TUI
python -m oce_sentry --once   # one fetch, console dump, exit
```

Prerequisites: Azure CLI signed in, PowerShell 7 (runbooks and kits execute
locally), and network reach to the report library, ADO, and Kusto/IcM.

**No daemon. No instance directory. No pipeline hosting.** If the console ever
requires any of those, that is a bug — see `DATA-MAP.md` for how portability is
maintained.

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
