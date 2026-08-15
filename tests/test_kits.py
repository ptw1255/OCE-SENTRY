"""Kits: playbooks over skills.

The tests that matter here are about honesty. A kit that silently runs short,
or that quietly contains a skill with side effects, is worse than no kit --
the operator trusts a playbook's output more than an ad-hoc run, so it has to
deserve that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oce_sentry.kits import (
    KIT_POLICY,
    Kit,
    KitRun,
    KitStep,
    find_kit,
    load_kits,
)
from oce_sentry.models import Incident


def _definitions() -> list[dict]:
    return json.loads(KIT_POLICY.read_text(encoding="utf-8"))["kits"]


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="850000001",
        title="Analysis module QoS below acceptable levels",
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-not-auto",
        monitor_id="AnalysisModuleQos",
        owning_team_id="104519",
        owning_team_name="SHAREPOINTSNAP",
        owning_contact_alias="alias",
        create_date="2026-01-01T00:00:00Z",
        mitigate_date=None,
        mitigated_by=None,
        is_terminal=False,
        minutes_open=60.0,
        is_customer_impacting=False,
        env_class="PROD",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


# ------------------------------------------------------------------ policy


def test_every_kit_has_the_fields_an_operator_reads():
    for entry in _definitions():
        assert entry["id"] and entry["name"]
        # The question is the whole point of a kit: it is what the operator
        # scans for. A kit without one is just a list of skills again.
        assert entry["question"].strip()
        assert entry["when"].strip()
        assert entry["skills"], entry["id"]


def test_kit_ids_are_unique():
    ids = [e["id"] for e in _definitions()]
    assert len(ids) == len(set(ids))


def test_kits_stay_small():
    """Past about four skills the operator stops reading the output."""
    for entry in _definitions():
        assert len(entry["skills"]) <= 4, entry["id"]


def test_no_kit_batches_a_writing_skill():
    """Writes stay deliberate and single.

    A batch run is the worst place to discover a side effect, because the
    operator is not reading each step before it happens.
    """
    writers = {
        "log-work-item",
        "resolve-work-item",
        "icm-tag",
        "send-aircover",
        "csam-notify",
        "scrub-cri2lsi",
        "shd-post",
    }
    for entry in _definitions():
        assert not (writers & set(entry["skills"])), entry["id"]


def test_kit_skill_lists_have_no_duplicates():
    for entry in _definitions():
        assert len(entry["skills"]) == len(set(entry["skills"])), entry["id"]


# ---------------------------------------------------------------- resolution


def test_kits_load_even_with_no_skills_installed(monkeypatch):
    """An operator who cloned nothing still sees which kits exist and why they are empty."""
    monkeypatch.delenv("OCE_SENTRY_SKILLS", raising=False)
    kits = load_kits(None)
    assert len(kits) == len(_definitions())
    assert all(not k.available for k in kits)
    assert all(k.missing == k.skill_ids for k in kits)
    assert all("0 of" in k.coverage for k in kits)


def test_missing_policy_file_is_not_fatal(tmp_path):
    assert load_kits(None, tmp_path / "absent.json") == []


def test_malformed_policy_file_is_not_fatal(tmp_path):
    broken = tmp_path / "kits.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_kits(None, broken) == []


def test_find_kit_returns_none_for_unknown(monkeypatch):
    monkeypatch.delenv("OCE_SENTRY_SKILLS", raising=False)
    assert find_kit(None, "no-such-kit") is None
    assert find_kit(None, "first-look") is not None


def test_coverage_reports_partial_installs():
    kit = Kit(
        id="k",
        name="K",
        question="q",
        when="w",
        skill_ids=["a", "b", "c"],
        skills=[object()],  # type: ignore[list-item]
        missing=["b", "c"],
    )
    assert kit.coverage == "1 of 3 installed"
    assert kit.available
    assert not kit.complete


def test_kit_needs_an_incident():
    kit = Kit(id="k", name="K", question="q", when="w")
    assert not kit.applies(None)
    assert kit.applies(_incident())


# ------------------------------------------------------------------- results


def test_run_summary_counts_only_skills_that_ran():
    run = KitRun(
        kit_id="first-look",
        incident_id="850000001",
        steps=[
            KitStep(skill_id="icm", ok=True, duration_ms=4000),
            KitStep(skill_id="impact", ok=False, error="boom", duration_ms=1000),
            KitStep(skill_id="outage-pattern", ok=False, skipped=True),
        ],
        cancelled=True,
    )
    assert "1/2 skills answered" in run.summary()
    assert "cancelled" in run.summary()
    assert not run.ok


def test_empty_run_is_not_reported_as_success():
    assert not KitRun(kit_id="k", incident_id="1").ok


# --------------------------------------------------------------- integration


@pytest.mark.skipif(
    not __import__("os").environ.get("OCE_SENTRY_SKILLS"),
    reason="needs the ODSP ADO skill repositories cloned and OCE_SENTRY_SKILLS set",
)
def test_every_declared_skill_exists_on_a_configured_machine():
    """Catches a typo or a renamed skill before an operator does, mid-incident."""
    unresolved = {k.id: k.missing for k in load_kits(None) if k.missing}
    assert not unresolved, unresolved
