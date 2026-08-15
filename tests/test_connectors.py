"""Connectors: what a skill can reach, and whether Sentry passes it along."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oce_sentry.connectors import (
    Connector,
    annotate_requirements,
    config_path,
    load_connectors,
    mcp_enabled,
    probe,
    status_summary,
)
from oce_sentry.copilot import build_command
from oce_sentry.models import Incident
from oce_sentry.packs import ContextPack
from oce_sentry.skills import Skill

_CONFIG = {
    "mcpServers": {
        "azure": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@azure/mcp@2.0.5", "server", "start", "--read-only"],
        },
        "drdashboard": {"type": "http", "url": "https://example.invalid/mcp"},
        "icm": {"type": "stdio", "command": "python", "args": ["tools/icm_proxy.py"]},
    }
}


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(_CONFIG), encoding="utf-8")
    return path


def _incident() -> Incident:
    return Incident(
        incident_id="850000001", title="t", severity=2.0, severity_raw=2,
        status="ACTIVE", incident_type="LiveSite", track_reason="r",
        monitor_id="M", owning_team_id="1", owning_team_name="T",
        owning_contact_alias="a", create_date="2026-01-01T00:00:00Z",
        mitigate_date=None, mitigated_by=None, is_terminal=False,
        minutes_open=1.0, is_customer_impacting=False, env_class="PROD", tsg_id="",
    )


# ------------------------------------------------------------------- loading


def test_servers_are_read_from_the_config(config_file):
    names = {c.name for c in load_connectors(None, config_file)}
    assert names == {"azure", "drdashboard", "icm"}


def test_stdio_target_keeps_the_whole_command(config_file):
    azure = next(c for c in load_connectors(None, config_file) if c.name == "azure")
    assert azure.kind == "stdio"
    assert azure.command == "npx"
    assert "--read-only" in azure.target


def test_http_target_is_the_url(config_file):
    dr = next(c for c in load_connectors(None, config_file) if c.name == "drdashboard")
    assert dr.kind == "http"
    assert dr.target == "https://example.invalid/mcp"


def test_vscode_style_config_is_also_read(tmp_path):
    """`.vscode/mcp.json` uses `servers`; the root file uses `mcpServers`."""
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"icm": {"command": "python"}}}), encoding="utf-8")
    assert [c.name for c in load_connectors(None, path)] == ["icm"]


def test_missing_and_malformed_configs_are_not_fatal(tmp_path):
    assert load_connectors(None, tmp_path / "absent.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_connectors(None, broken) == []


def test_override_wins_over_discovery(tmp_path, monkeypatch):
    path = tmp_path / "custom.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OCE_SENTRY_MCP_CONFIG", str(path))
    assert config_path(None) == path


def test_override_pointing_nowhere_is_reported_as_absent(monkeypatch):
    """Better to say no config than to silently use a different one."""
    monkeypatch.setenv("OCE_SENTRY_MCP_CONFIG", "/no/such/file.json")
    assert config_path(None) is None


# ------------------------------------------------------------------- probing


def test_a_command_not_on_path_is_missing(tmp_path):
    connector = Connector(name="x", kind="stdio", target="definitely-not-a-real-binary-xyz")
    probe(connector)
    assert connector.status == "missing"
    assert "not on PATH" in connector.detail


def test_a_relative_script_must_exist_beside_its_config(tmp_path):
    """python is on PATH everywhere; the proxy it launches is not.

    The IcM proxy is declared as a relative path, so it only resolves when the
    working directory is the repository that declared it. Reporting "ready" on
    the strength of python existing would be wrong on most machines.
    """
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps(_CONFIG), encoding="utf-8")
    icm = next(c for c in load_connectors(None, config) if c.name == "icm")
    probe(icm)
    assert icm.status == "missing"
    assert "script not found" in icm.detail

    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "icm_proxy.py").write_text("", encoding="utf-8")
    icm = next(c for c in load_connectors(None, config) if c.name == "icm")
    probe(icm)
    assert icm.status == "ready"


def test_an_unreachable_endpoint_is_not_reported_as_ready(config_file):
    dr = next(c for c in load_connectors(None, config_file) if c.name == "drdashboard")
    probe(dr, timeout=2.0)
    assert dr.status == "unreachable"


def test_summary_counts_only_ready(config_file):
    connectors = load_connectors(None, config_file)
    connectors[0].status = "ready"
    connectors[1].status = "missing"
    connectors[2].status = "unreachable"
    assert status_summary(connectors) == "1 of 3 ready"


def test_summary_says_so_when_there_is_no_config():
    assert status_summary([]) == "no MCP config found"


# -------------------------------------------------------------- requirements


def _skill(skill_id: str, body: str) -> Skill:
    return Skill(
        id=skill_id, name=skill_id, description="", body=body,
        source="ado", directory=Path("."),
    )


def test_requirements_are_read_from_skill_prose(config_file):
    """Skills declare prerequisites in English, not front matter."""
    connectors = load_connectors(None, config_file)
    annotate_requirements(
        connectors,
        [
            _skill("correlation-ai", "This skill requires Kusto MCP servers."),
            _skill("handover", "Summarise the incident."),
        ],
    )
    azure = next(c for c in connectors if c.name == "azure")
    assert azure.required_by == ["correlation-ai"]


def test_a_connector_nothing_needs_reports_none(config_file):
    connectors = load_connectors(None, config_file)
    annotate_requirements(connectors, [_skill("handover", "Summarise.")])
    assert all(not c.required_by for c in connectors)


# ------------------------------------------------------------------- wiring


def test_connectors_are_off_by_default(monkeypatch, tmp_path):
    """Reaching production telemetry is a decision, not a default."""
    monkeypatch.delenv("OCE_SENTRY_ENABLE_MCP", raising=False)
    assert not mcp_enabled()

    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    command = build_command(
        _skill("s", "body"), _incident(), pack
    )
    assert "--additional-mcp-config" not in command


def test_enabling_passes_the_config_to_copilot(monkeypatch, tmp_path, config_file):
    monkeypatch.setenv("OCE_SENTRY_ENABLE_MCP", "1")
    monkeypatch.setenv("OCE_SENTRY_MCP_CONFIG", str(config_file))
    assert mcp_enabled()

    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    command = build_command(_skill("s", "body"), _incident(), pack)
    assert "--additional-mcp-config" in command
    # The CLI takes a file path only when prefixed with @.
    assert f"@{config_file}" in command


def test_enabling_without_a_config_does_not_pass_a_broken_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("OCE_SENTRY_ENABLE_MCP", "1")
    monkeypatch.setenv("OCE_SENTRY_MCP_CONFIG", "/no/such/file.json")
    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    command = build_command(_skill("s", "body"), _incident(), pack)
    assert "--additional-mcp-config" not in command


def test_connectors_do_not_loosen_the_shell_rule(monkeypatch, tmp_path, config_file):
    """Wiring data access must not become a way to acquire shell."""
    monkeypatch.setenv("OCE_SENTRY_ENABLE_MCP", "1")
    monkeypatch.setenv("OCE_SENTRY_MCP_CONFIG", str(config_file))
    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    command = build_command(_skill("s", "body"), _incident(), pack)
    assert "--deny-tool" in command and "shell" in command
    assert "--allow-tool" not in command
