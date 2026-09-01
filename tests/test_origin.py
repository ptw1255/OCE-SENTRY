"""Skill origins.

A payload names skills by absolute path, which is right on the machine that
built it and useless anywhere else. These tests hold that the Azure DevOps
origin travels with it, and that nothing secret travels with it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from oce_sentry.origin import (
    ADO_RESOURCE,
    KNOWN_REPOS,
    Origin,
    clone_instructions,
    origin_of,
)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A real git repository, so the reader is exercised rather than mocked."""
    repo = tmp_path / "SRELivesite-RCAAgent"
    skills = repo / "services" / "spo" / "sre" / "skills" / "icm"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# icm", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    git(
        "remote",
        "add",
        "origin",
        "https://dev.azure.com/onedrive/SPARC/_git/SRELivesite-RCAAgent",
    )
    git("add", "-A")
    git("commit", "-qm", "seed")

    origin_of.__wrapped__ if hasattr(origin_of, "__wrapped__") else None
    from oce_sentry import origin as module

    module._repo_root.cache_clear()
    module._repo_facts.cache_clear()
    return skills


# ------------------------------------------------------------------ reading


def test_it_reads_the_remote_branch_and_commit(checkout):
    found = origin_of(checkout)
    assert found.repo == "SRELivesite-RCAAgent"
    assert found.url.endswith("/SRELivesite-RCAAgent")
    assert found.branch
    assert re.fullmatch(r"[0-9a-f]{7,}", found.commit)


def test_it_records_the_path_within_the_repository(checkout):
    """Forward-slashed, because that is how ADO addresses a file."""
    assert origin_of(checkout).path == "services/spo/sre/skills/icm"


def test_a_directory_outside_git_has_no_origin(tmp_path):
    """Stated rather than guessed: an unknown origin is information."""
    plain = tmp_path / "loose-skills"
    plain.mkdir()
    found = origin_of(plain)
    assert not found.known
    assert found.url == ""


def test_none_is_handled():
    assert origin_of(None).known is False


def test_the_web_url_points_at_the_branch(checkout):
    url = origin_of(checkout).web_url
    assert url.startswith("https://dev.azure.com/onedrive/SPARC/_git/")
    assert "path=/services/spo/sre/skills/icm" in url
    assert "version=GB" in url


def test_no_web_url_without_an_origin():
    assert Origin().web_url == ""


# ------------------------------------------------------------------ cloning


def test_every_known_repo_can_be_cloned():
    steps = clone_instructions()
    assert {s["repo"] for s in steps} == set(KNOWN_REPOS)
    for step in steps:
        assert step["url"].startswith("https://dev.azure.com/onedrive/SPARC/_git/")
        assert step["clone"].startswith("git ")


def test_the_clone_uses_a_token_substitution_not_a_token():
    """A manifest is written to disk and read elsewhere.

    Embedding a real bearer token would put a live {Credential} in a file whose
    whole purpose is to be handed to something else.
    """
    command = clone_instructions(["ODSP-SRE-AI-Skills"])[0]["clone"]
    assert "$(az account get-access-token" in command
    assert ADO_RESOURCE in command
    # Nothing resembling a JWT.
    assert not re.search(r"ey[A-Za-z0-9_-]{20,}", command)


def test_asking_for_an_unknown_repo_yields_nothing():
    assert clone_instructions(["not-a-repo"]) == []


def test_the_known_repos_declare_where_their_skills_live():
    for name, spec in KNOWN_REPOS.items():
        assert spec["skillPaths"], name
        assert all(not p.startswith("/") for p in spec["skillPaths"]), name
