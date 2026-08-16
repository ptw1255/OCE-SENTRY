"""Where a skill came from.

A payload names skills by absolute path, which is correct for the machine that
built it and useless anywhere else. Recording the Azure DevOps origin as well
means the manifest stays actionable when the file is not there: an agent that
cannot read the path knows the repository, the branch, the commit it was read
at, and the path within the repository.

Everything here is read from the checkout itself -- `git config`, `git
rev-parse` -- so it is observed rather than configured. A directory that is not
a git checkout simply has no origin, which is stated rather than guessed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: The two ODSP repositories Sentry expects skills to come from. Used only to
#: build a clone instruction for a machine that has neither; discovery of an
#: existing checkout never consults this.
KNOWN_REPOS: dict[str, dict[str, str]] = {
    "SRELivesite-RCAAgent": {
        "url": "https://dev.azure.com/onedrive/SPARC/_git/SRELivesite-RCAAgent",
        "organization": "onedrive",
        "project": "SPARC",
        "skillPaths": [
            ".github/skills",
            "services/spo/sre/skills",
            "services/spo/meta/skills",
        ],
    },
    "ODSP-SRE-AI-Skills": {
        "url": "https://dev.azure.com/onedrive/SPARC/_git/ODSP-SRE-AI-Skills",
        "organization": "onedrive",
        "project": "SPARC",
        "skillPaths": ["skills"],
    },
}

#: Azure DevOps needs a bearer token for a non-interactive clone; interactive
#: auth is disabled on these repositories.
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


@dataclass
class Origin:
    """Where a directory came from, as its checkout reports it."""

    repo: str = ""
    url: str = ""
    branch: str = ""
    commit: str = ""
    #: Path within the repository, forward-slashed, as ADO writes it.
    path: str = ""

    @property
    def known(self) -> bool:
        return bool(self.url)

    @property
    def web_url(self) -> str:
        """A link a human can open to read the file in ADO."""
        if not self.url or not self.path:
            return ""
        return f"{self.url}?path=/{self.path}&version=GB{self.branch or 'main'}"


def _git(directory: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@lru_cache(maxsize=64)
def _repo_root(directory: str) -> str:
    return _git(Path(directory), "rev-parse", "--show-toplevel")


@lru_cache(maxsize=64)
def _repo_facts(root: str) -> tuple[str, str, str]:
    path = Path(root)
    url = _git(path, "config", "--get", "remote.origin.url")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git(path, "rev-parse", "--short", "HEAD")
    return url, branch, commit


def origin_of(directory: Path | None) -> Origin:
    """The ADO origin of a checked-out directory.

    Cached per repository root: a payload with a dozen skills from the same
    checkout should not shell out to git a dozen times.
    """
    if directory is None:
        return Origin()
    root = _repo_root(str(directory))
    if not root:
        return Origin()

    url, branch, commit = _repo_facts(root)
    try:
        relative = directory.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        relative = ""

    return Origin(
        repo=Path(root).name,
        url=url,
        branch=branch,
        commit=commit,
        path=relative,
    )


def clone_instructions(missing: list[str] | None = None) -> list[dict[str, str]]:
    """How to obtain the skill repositories on a machine that lacks them.

    Written as data rather than prose so the consumer can act on it: an agent
    reading a manifest whose skill paths do not resolve has everything it needs
    to fetch them without being told in English.
    """
    wanted = missing if missing is not None else list(KNOWN_REPOS)
    steps: list[dict[str, str]] = []
    for name in wanted:
        spec = KNOWN_REPOS.get(name)
        if not spec:
            continue
        steps.append(
            {
                "repo": name,
                "url": spec["url"],
                "clone": (
                    'git -c http.extraheader="Authorization: Bearer '
                    "$(az account get-access-token --resource "
                    f"{ADO_RESOURCE} --query accessToken -o tsv)\" "
                    f"clone {spec['url']}"
                ),
                "note": (
                    "Azure DevOps requires a bearer token; interactive auth is "
                    "disabled on these repositories."
                ),
            }
        )
    return steps
