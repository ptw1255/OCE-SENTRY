"""Context packs: what a skill is given, and where it came from.

The pipeline these cover is the reason connectors can stay off. A query kit
runs verified KQL with the operator's own {Credential}; its output lands on
disk; the next skill run reads real rows without touching a cluster.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oce_sentry.models import Incident
from oce_sentry.packs import (
    MAX_KIT_RESULTS,
    build_pack,
    load_kit_results,
    prune_output,
    storage_footprint,
)


class _Config:
    def __init__(self, tmp_path: Path):
        self.state_dir = tmp_path / "state"
        self.output_dir = tmp_path / "output"


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="850000001", title="t", severity=2.0, severity_raw=2,
        status="ACTIVE", incident_type="LiveSite", track_reason="r",
        monitor_id="m", owning_team_id="1", owning_team_name="T",
        owning_contact_alias="a", create_date="2026-01-01T00:00:00Z",
        mitigate_date=None, mitigated_by=None, is_terminal=False,
        minutes_open=60.0, is_customer_impacting=False, env_class="PROD",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


def _persist_result(
    config: _Config,
    incident_id: str,
    action_id: str,
    body: str = "125 row(s):\nMonitorId  Incidents\nLSLA013  2091\n",
    age: timedelta = timedelta(0),
    sidecar_extra: dict | None = None,
) -> Path:
    """Write a run the way actions._persist does."""
    target = config.output_dir / incident_id
    target.mkdir(parents=True, exist_ok=True)
    stem = f"20260101T000000-{action_id}-abc123"
    stdout = target / f"{stem}.stdout.txt"
    stdout.write_text(body, encoding="utf-8")
    meta = {"actionId": action_id, "incidentId": incident_id}
    meta.update(sidecar_extra or {})
    (target / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")

    if age:
        when = (datetime.now(timezone.utc) - age).timestamp()
        import os

        # Both files are written together by actions._persist, so they age
        # together. Ageing only one produced an orphaned sidecar that no real
        # run can create.
        os.utime(stdout, (when, when))
        os.utime(target / f"{stem}.json", (when, when))
    return stdout


# ----------------------------------------------------------------- discovery


def test_nothing_found_when_no_kit_has_run(tmp_path):
    config = _Config(tmp_path)
    assert load_kit_results(_incident(), config) == []


def test_a_persisted_result_is_found(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "sev3-alertstorm")
    found = load_kit_results(_incident(), config)
    assert [action for action, _ in found] == ["sev3-alertstorm"]
    assert "LSLA013" in found[0][1]


def test_results_for_another_incident_are_not_borrowed(tmp_path):
    """Evidence is about one incident. Mixing them would be a citation error."""
    config = _Config(tmp_path)
    _persist_result(config, "999999999", "sev3-alertstorm")
    assert load_kit_results(_incident(), config) == []


def test_skill_runs_are_not_treated_as_measurements(tmp_path):
    """A skill's answer is prose, not rows.

    Feeding one model's output to the next as evidence is how a guess turns
    into a citation, so only runs carrying an actionId are collected.
    """
    config = _Config(tmp_path)
    target = config.output_dir / "850000001"
    target.mkdir(parents=True)
    (target / "run.stdout.txt").write_text("some answer", encoding="utf-8")
    (target / "run.json").write_text(
        json.dumps({"skillId": "outage-pattern"}), encoding="utf-8"
    )
    assert load_kit_results(_incident(), config) == []


def test_stale_output_is_not_offered_as_current_evidence(tmp_path):
    """A week-old row set from the same monitor describes a different firing."""
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "old-kit", age=timedelta(days=3))
    assert load_kit_results(_incident(), config) == []


def test_the_age_limit_is_adjustable(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "old-kit", age=timedelta(days=3))
    found = load_kit_results(_incident(), config, max_age=timedelta(days=7))
    assert [action for action, _ in found] == ["old-kit"]


def test_empty_output_is_not_carried(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "quiet-kit", body="   \n")
    assert load_kit_results(_incident(), config) == []


def test_a_malformed_sidecar_is_skipped_not_fatal(tmp_path):
    config = _Config(tmp_path)
    target = config.output_dir / "850000001"
    target.mkdir(parents=True)
    (target / "broken.json").write_text("{not json", encoding="utf-8")
    _persist_result(config, "850000001", "good-kit")
    assert [a for a, _ in load_kit_results(_incident(), config)] == ["good-kit"]


def test_only_the_most_recent_are_carried(tmp_path):
    config = _Config(tmp_path)
    for index in range(MAX_KIT_RESULTS + 3):
        _persist_result(
            config, "850000001", f"kit-{index}", age=timedelta(minutes=index)
        )
    found = load_kit_results(_incident(), config)
    assert len(found) == MAX_KIT_RESULTS
    # Newest first: kit-0 was written with the smallest age.
    assert found[0][0] == "kit-0"


# ---------------------------------------------------------------------- pack


def test_the_pack_carries_persisted_results(tmp_path):
    """The whole point: a kit run reaches a later skill run."""
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "sev3-alertstorm")
    pack = build_pack(_incident(), config)
    carried = [f for f in pack.files if f.startswith("kit-results/")]
    assert carried == ["kit-results/00-sev3-alertstorm.txt"]
    assert "LSLA013" in (pack.directory / carried[0]).read_text(encoding="utf-8")


def test_the_context_announces_measured_results(tmp_path):
    """Otherwise the model has to notice a directory to know they exist."""
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "sev3-alertstorm")
    pack = build_pack(_incident(), config)
    context = (pack.directory / "context.md").read_text(encoding="utf-8")
    assert "Query results available in this pack" in context
    assert "sev3-alertstorm" in context
    assert "measured rows, not estimates" in context


def test_the_context_stays_quiet_when_there_are_none(tmp_path):
    config = _Config(tmp_path)
    context = (build_pack(_incident(), config).directory / "context.md").read_text(
        encoding="utf-8"
    )
    assert "Query results available" not in context


def test_loading_can_be_turned_off(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "sev3-alertstorm")
    pack = build_pack(_incident(), config, include_kit_results=False)
    assert not [f for f in pack.files if f.startswith("kit-results/")]


def test_a_fresh_run_is_not_duplicated_by_the_disk_copy(tmp_path):
    """Both paths write the same directory; the pack should list it once."""

    class _Run:
        action_id = "sev3-alertstorm"
        stdout = "fresh rows"

        def summary(self):
            return "ok in 3.7s"

    config = _Config(tmp_path)
    _persist_result(config, "850000001", "sev3-alertstorm")
    pack = build_pack(_incident(), config, kit_runs=[_Run()])
    carried = [f for f in pack.files if f.startswith("kit-results/")]
    assert len(carried) == 1
    assert "fresh rows" in (pack.directory / carried[0]).read_text(encoding="utf-8")


# ------------------------------------------------------------------ storage


def test_footprint_does_not_double_count_nested_output(tmp_path):
    """output_dir defaults to a subdirectory of state_dir.

    Counting both roots naively reported roughly twice the real footprint,
    which is exactly the number an operator would act on.
    """
    config = _Config(tmp_path)
    config.output_dir = config.state_dir / "output"
    (config.state_dir / "output" / "850000001").mkdir(parents=True)
    (config.state_dir / "output" / "850000001" / "a.txt").write_text(
        "x" * 1000, encoding="utf-8"
    )
    size, count = storage_footprint(config)
    assert count == 1
    assert size == 1000


def test_footprint_is_zero_before_anything_runs(tmp_path):
    assert storage_footprint(_Config(tmp_path)) == (0, 0)


def test_old_output_is_pruned(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "old", age=timedelta(days=60))
    assert prune_output(config) > 0
    assert not (config.output_dir / "850000001").exists()


def test_recent_output_is_kept(tmp_path):
    config = _Config(tmp_path)
    _persist_result(config, "850000001", "recent")
    assert prune_output(config) == 0
    assert (config.output_dir / "850000001").is_dir()


def test_pruning_a_missing_directory_is_not_fatal(tmp_path):
    assert prune_output(_Config(tmp_path)) == 0
