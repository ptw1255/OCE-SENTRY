# Data map

Every source the console reads, what it carries, how it is reached, and how it
is allowed to fail. This is the document to update when a source moves.

The rule underneath all of it: **the console reads artifacts the fleet already
produced. It does not measure anything.** The fleet's guarantee is that every
number in every report comes from a deterministic query recorded in that run's
evidence bundle. A console that recomputed a number would eventually disagree
with a report someone had already been sent.

---

## The producer

The **MeTA live site agent fleet** (`meta-livesite-agent-expander`) runs five
scheduled loops. Three of them produce everything this console displays.

| Loop | Cadence | Produces |
| --- | --- | --- |
| `incident-watchlist` | 20 min | The tracked queue: what is active and in scope, and how long it has been |
| `incident-response` | 20 min | Per-incident report, scope verdict, staged enrichment, and the link record tying them together |
| `incident-aggregate` | 20 min | The rolled-up open-incident report |
| `noise-triage` | 20 min | ADO bugs for monitors that page without being actionable |
| `learning-loop` | 20 min | Improvement proposals about the fleet itself (not consumed here) |

## Sources

### 1. Report manifest — the primary source

The richest source, and the one designed for exactly this purpose. The fleet
writes `index.json` into the MeTA-SRE-Comms document library and **reads it
back from the library rather than from local state**, precisely so that
consumers can reach it.

| | |
| --- | --- |
| **Reached via** | Microsoft Graph, library folder `Incident Reports/` |
| **Auth** | Ambient Azure identity (`az login`) to a Graph token |
| **Schema** | `meta-livesite-report-manifest/v1` |
| **Written by** | `publish-report-index.ps1`, every `incident-response` run |

Per-report entry:

```json
{
  "incidentId": "<id>",
  "status": "<icm status>",
  "scopeVerdict": "Undetermined-to-contained, leaning contained.",
  "reportUrl": "https://<tenant>/.../Incident%20Reports/<yyyy-MM>/<id>.html",
  "openBugs": [ { "workItemId": 0, "monitorId": "<monitor>", "state": "New" } ],
  "updatedAt": "<iso8601>",
  "announced": false,
  "announcedAt": ""
}
```

Plus a top-level `pendingAnnouncement` list.

Two properties matter to the console:

- **It is a work queue, not an archive.** Entries are retained while
  unannounced or non-terminal, and content-hashed so it only churns on
  substantive change.
- **Consumers write back.** `announced` is set by a downstream consumer and the
  fleet preserves it: *"this fleet only ever resets announced to false, and
  only when a report's substantive content has changed."* A second consumer is
  an anticipated case, not an intrusion.

**Used for:** incident detail, scope verdict, report link, per-incident bug
list, and the "has this been communicated yet" signal that exists nowhere in
anyone's current workflow.

### 2. The live incident queue — Kusto, queried directly

**The queue is queried live rather than read from a published artifact.** This
is the console's one deliberate departure from "read what the fleet produced",
and it is worth stating plainly because the alternative was reasonable too.

| | |
| --- | --- |
| **Reached via** | Kusto REST, `IcmDataWarehouse` / `IncidentsSnapshotV2` |
| **Auth** | Ambient Azure identity |
| **Scope policy** | The fleet's `data-paths.json`, read at startup |

The KQL is **built from the fleet's own policy file** using the same
construction as `collect-watchlist.ps1`: same team list, same severity branches,
same environment classifier, same `case()` precedence. Parity is therefore by
construction rather than by reimplementation, and it is asserted by a live test
that compares exact incident ids and track reasons — not counts.

Why live rather than the fleet's published state:

- An OCE reading a queue that is up to 20 minutes old during an active incident
  is the exact failure this console exists to remove.
- It removes the dependency on the fleet's machine entirely. The queue works
  from any machine with `az login` and a repo checkout.

What this costs, and how it is handled: the fleet's state carries tracking
history (`runsTracked`, `firstTrackedAt`, `isNew`) that a live query cannot
know. Where `watchlist-state/watchlist.json` happens to be reachable it is read
as **enrichment only** — it adds "the fleet has looked at this 25 times" to a
row. Its absence is normal and changes nothing about which incidents appear.

There is no built-in scope fallback. If the policy file cannot be read the
console refuses to start, because silently running a stale copy of the fleet's
scope would produce a confidently wrong queue.

### 3. Fleet health — not yet reachable off-box

| | |
| --- | --- |
| **Location today** | `<instance>/runs/`, `telemetry.db` — local |
| **Reached via** | Nothing. Same gap as the watchlist |

Needed because a stale watchlist silently degrades every other tab. The
distinction that matters, and that must survive publication: **aborted-as-no-work
(healthy — most runs park by design) versus actually failed.** Collapsing both
into "not successful" makes the tab misleading in exactly the way it exists to
prevent.

### 4. Investigation kits and runbooks

See **[RUNBOOK-SOURCES.md](RUNBOOK-SOURCES.md)**. The console loads these from
configured sources and never vendors them.

### 5. Azure DevOps

| | |
| --- | --- |
| **Reached via** | ADO REST, ambient Azure identity |
| **Used for** | Live state of the noise bugs the fleet filed |

The manifest carries each bug's last-known state; ADO carries its current one.
A bug that moved to Done should stop occupying attention, so the console
refreshes state rather than trusting the cached value.

Board mapping lives in the fleet's `data-paths.json`.

### 6. IcM and Kusto

| | |
| --- | --- |
| **Reached via** | IcM APIs; Kusto (IcM cluster, telemetry cluster) |
| **Used for** | Incident detail, discussion, and kit query execution |

Kit execution is the only path that issues live queries, and only on an
explicit keypress. Everything else is an artifact read.

---

## Freshness and failure

Two rules, both learned from defects already found in the fleet.

**1. Age is measured from the document's own timestamp, never from fetch time.**
Every published artifact carries `lastRunUtc`, `collectedAtUtc` or
`generatedAt`. A fresh fetch of a stale document is stale data; using fetch
time would hide a dead upstream loop behind a healthy-looking cache.

**2. Every source fails independently and visibly.** The library being
unreachable must not blank the bug list. Each tab shows its own source age and
its own failure. Offline is a supported state with clearly-marked last-known
data, not a crash.

## Portability

| Source | Reachable from any machine? |
| --- | --- |
| Report manifest | Yes — Graph |
| Watchlist tracking history (enrichment only) | No — instance-local, and optional |
| ADO bugs | Yes — ADO REST |
| IcM / Kusto | Yes — APIs |
| Kits / runbooks | Yes — configured sources |
| Live incident queue | Yes — Kusto, built from the fleet's scope policy |
| **Fleet health** | **No — instance-local** |

Closing those two gaps is what makes the console independent of *where the
fleet runs*. Once the queue and fleet status are published to the library, a
later move to cloud hosting changes nothing for any consumer: the endpoints,
the auth and the schemas are identical. That is why publishing is worth doing
before the console is built rather than after.

