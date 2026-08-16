# Setting up on a new machine

Verified by stripping every `OCE_SENTRY_*` variable and pointing each discovery
path at an empty directory, then checking what still worked.

---

## The short version

```powershell
winget install Microsoft.AzureCLI          # if you do not have it
az login

pip install git+https://github.com/parkerwall_microsoft/oce-sentry.git
oce-sentry
```

That gets you the incident queue, the SLI view, the bug tracker, and a payload
carrying the incident's facts. Roughly a minute.

Everything else is optional and adds to the payload rather than being needed to
start.

---

## Required

| | Why | Check |
| --- | --- | --- |
| **Python 3.10+** | The console is a Python package | `python --version` |
| **Azure CLI, logged in** | The only credential path. No PATs, no token files, no secrets on disk | `az account show` |

If `az login` has not been run, the queue reports the auth failure rather than
showing an empty list, because an empty queue and a broken queue look identical
and mean opposite things.

## Optional, in the order worth doing

### 1. Skills — the biggest single gain

Without them the payload has no `sequence`. With them it names the ODSP SRE
team's skills and where to load each one from.

```powershell
cd ~\repos
git clone https://onedrive@dev.azure.com/onedrive/SPARC/_git/SRELivesite-RCAAgent
git clone https://onedrive@dev.azure.com/onedrive/SPARC/_git/ODSP-SRE-AI-Skills

[Environment]::SetEnvironmentVariable('OCE_SENTRY_SKILLS', (@(
  "$HOME\repos\ODSP-SRE-AI-Skills\skills",
  "$HOME\repos\SRELivesite-RCAAgent\.github\skills",
  "$HOME\repos\SRELivesite-RCAAgent\services\spo\sre\skills",
  "$HOME\repos\SRELivesite-RCAAgent\services\spo\meta\skills"
) -join ';'), 'User')
```

This is the **only** variable that has to be set by hand; everything else below
is found automatically if the checkout is at `~\repos`.

> A User-scope variable does not reach a shell that was already open. Start a
> new terminal after setting it.

If the clone prompts for credentials, Azure DevOps needs a bearer token:

```powershell
$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv
git -c http.extraheader="Authorization: Bearer $token" clone <url>
```

### 2. The RCA agent checkout — connectors and access

Cloned in step 1. Sentry reads two things from it automatically:

- `.mcp.json` — the connector inventory shown in Settings (`!`)
- `ONBOARDING.md` — the entitlement each cluster needs, and the request link

Without it, Settings falls back to a built-in snapshot of the access table and
says so, because an entitlement that has been renamed and is still displayed
sends someone to an approver who will reject them.

### 3. The fleet checkout — investigation queries

```powershell
cd ~\repos
git clone https://github.com/parkerwall_microsoft/meta-livesite-agent-expander.git
```

Adds the `access.queries` block: verified KQL with the cluster, database and
incident window already resolved. Found automatically at `~\repos`.

---

## What you get at each stage

Measured, not estimated:

| Setup | Queue | Payload contains |
| --- | --- | --- |
| `az login` only | 27 incidents | incident facts, window, report path (~2.3 KB) |
| \+ skills | 27 incidents | \+ a `sequence` of skills with absolute `SKILL.md` paths |
| \+ fleet kits | 27 incidents | \+ resolved queries, base rates, cluster access |

Nothing degrades into a wrong answer. A missing source produces an empty
section, not a plausible-looking guess.

---

## Not required

- **GitHub Copilot CLI.** Sentry builds payloads; it does not run agents. The
  OCE runs their own CLI against the payload.
- **MCP servers running.** Settings reports whether each *could* start. They
  are not launched by Sentry.
- **Any PAT or secret.** There is no configuration file with credentials in it,
  and nothing is written outside `%LOCALAPPDATA%\oce-sentry`.

---

## Verifying an install

```powershell
oce-sentry --once          # the queue, headless
oce-sentry --connectors    # connectors, clusters, and the access each needs
oce-sentry --skills        # what was discovered
```

`--connectors` is the most useful of the three: it prints every cluster, whether
its command resolves on this machine, and the entitlement to request if not.

## Environment variables

Only the first normally needs setting.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OCE_SENTRY_SKILLS` | — | ADO skill directories, path-separated |
| `OCE_SENTRY_KITS` | `~\repos\meta-livesite-agent-expander\kits` | Investigation queries |
| `OCE_SENTRY_RCA_REPO` | `~\repos\SRELivesite-RCAAgent` | Connector and access reference |
| `OCE_SENTRY_MCP_CONFIG` | the RCA repo's `.mcp.json` | Connector inventory |
| `OCE_SENTRY_POLICY` | bundled | Incident scope. The console refuses to start if this cannot be read |
| `OCE_SENTRY_STATE_DIR` | `%LOCALAPPDATA%\oce-sentry` | Payloads, cache, output |
