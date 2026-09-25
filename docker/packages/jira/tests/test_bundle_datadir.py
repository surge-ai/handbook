from __future__ import annotations

import json
from pathlib import Path

import jira_mock.state as state_mod
from jira_mock.state import (
    load_state,
    resolve_bundle_output_path,
    resolve_bundle_state_path,
    resolve_bundle_state_paths,
    set_agent_workspace,
    state_to_json,
)


def _flat_site(account_id: str) -> dict:
    """A minimal valid flat single-site seed keyed on a distinct user."""
    return {
        "currentUserAccountId": account_id,
        "users": {
            account_id: {
                "accountId": account_id,
                "accountType": "atlassian",
                "emailAddress": f"{account_id}@example.com",
                "displayName": f"User {account_id}",
                "active": True,
                "timeZone": "America/New_York",
            }
        },
    }


def test_resolve_bundle_state_path_prefers_state_json(tmp_path: Path, monkeypatch) -> None:
    """state.json wins over a bare *.json sibling in the per-service subdir."""
    service_dir = tmp_path / "services" / "jira"
    service_dir.mkdir(parents=True)
    (service_dir / "state.json").write_text("{}", encoding="utf-8")
    (service_dir / "issues.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() == service_dir / "state.json"


def test_resolve_bundle_state_path_globs_when_no_state_json(tmp_path: Path, monkeypatch) -> None:
    """The singular back-compat accessor returns the first *.json when there's
    no state.json. (The loader itself reads the whole folder — see
    resolve_bundle_state_paths.)"""
    service_dir = tmp_path / "services" / "jira"
    service_dir.mkdir(parents=True)
    (service_dir / "b.json").write_text("{}", encoding="utf-8")
    (service_dir / "a.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() == service_dir / "a.json"


def test_resolve_bundle_state_path_missing_subdir(tmp_path: Path, monkeypatch) -> None:
    """A partial bundle without this service's subdir resolves to None so the
    loader falls back to INPUTDIR."""
    (tmp_path / "services").mkdir()
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() is None
    monkeypatch.delenv("BUNDLEDIR")
    assert resolve_bundle_state_path() is None


def test_resolve_bundle_output_path(monkeypatch) -> None:
    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", "/some/output/services/jira")
    assert resolve_bundle_output_path() == Path("/some/output/services/jira/state.json")
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR")
    assert resolve_bundle_output_path() is None


def _reset_loader(workspace: Path) -> None:
    """Force a clean reload from env the way load_state() guards on globals."""
    state_mod._current_state = None
    state_mod._sites.clear()
    set_agent_workspace(str(workspace / "agent_workspace"))


def test_bundle_state_json_matches_inputdir(tmp_path: Path, monkeypatch) -> None:
    """Loading the same seed from <BUNDLEDIR>/services/jira/state.json yields the
    same canonical state as loading it from INPUTDIR."""
    for var in ("BUNDLEDIR", "INPUTDIR", "OUTPUTDIR", "BUNDLE_OUTPUT_DIR"):
        monkeypatch.delenv(var, raising=False)

    seed = {
        "currentUserAccountId": "user-1",
        "users": {
            "user-1": {
                "accountId": "user-1",
                "accountType": "atlassian",
                "emailAddress": "user-1@example.com",
                "displayName": "User 1",
                "active": True,
                "timeZone": "America/New_York",
            }
        },
    }
    seed_text = json.dumps(seed)

    bundle_dir = tmp_path / "bundle"
    bundle_state = bundle_dir / "services" / "jira" / "state.json"
    bundle_state.parent.mkdir(parents=True)
    bundle_state.write_text(seed_text, encoding="utf-8")

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "jira.json").write_text(seed_text, encoding="utf-8")

    # Load from bundle (INPUTDIR unset).
    _reset_loader(tmp_path / "bundle_ws")
    monkeypatch.setenv("BUNDLEDIR", str(bundle_dir))
    monkeypatch.delenv("INPUTDIR", raising=False)
    load_state()
    from_bundle = state_to_json()

    # Load from INPUTDIR (BUNDLEDIR unset).
    _reset_loader(tmp_path / "input_ws")
    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.setenv("INPUTDIR", str(input_dir))
    load_state()
    from_inputdir = state_to_json()

    assert from_bundle == from_inputdir


def test_resolve_bundle_state_paths_returns_whole_folder(tmp_path: Path, monkeypatch) -> None:
    """The plural resolver returns ALL sorted *.json when there's no state.json,
    and just [state.json] when one is present."""
    service_dir = tmp_path / "services" / "jira"
    service_dir.mkdir(parents=True)
    (service_dir / "b.json").write_text("{}", encoding="utf-8")
    (service_dir / "a.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert resolve_bundle_state_paths() == [service_dir / "a.json", service_dir / "b.json"]

    (service_dir / "state.json").write_text("{}", encoding="utf-8")
    assert resolve_bundle_state_paths() == [service_dir / "state.json"]


def test_bundle_multifile_folder_matches_consolidated_state(tmp_path: Path, monkeypatch) -> None:
    """A split multi-file bundle folder coalesces to the same canonical state as a
    single consolidated multi-site state.json."""
    for var in ("BUNDLEDIR", "INPUTDIR", "OUTPUTDIR", "BUNDLE_OUTPUT_DIR"):
        monkeypatch.delenv(var, raising=False)

    site_a = _flat_site("user-a")
    site_b = _flat_site("user-b")
    # Make site B distinguishable beyond its current user via an extra user.
    site_b["users"]["user-b-extra"] = {
        "accountId": "user-b-extra",
        "accountType": "atlassian",
        "emailAddress": "user-b-extra@example.com",
        "displayName": "User B Extra",
        "active": True,
        "timeZone": "America/New_York",
    }

    # (a) consolidated: one state.json with both sites.
    consolidated = tmp_path / "consolidated"
    consolidated_state = consolidated / "services" / "jira" / "state.json"
    consolidated_state.parent.mkdir(parents=True)
    consolidated_state.write_text(
        json.dumps({"sites": {"default": site_a, "extra": site_b}}),
        encoding="utf-8",
    )

    # (b) split: two {sites} wrapper files, no state.json — two distinct named
    # sites across files (vs. the flat-file case, which merges into one site).
    split = tmp_path / "split"
    split_dir = split / "services" / "jira"
    split_dir.mkdir(parents=True)
    (split_dir / "a.json").write_text(json.dumps({"sites": {"default": site_a}}), encoding="utf-8")
    (split_dir / "b.json").write_text(json.dumps({"sites": {"extra": site_b}}), encoding="utf-8")

    # Load consolidated.
    _reset_loader(tmp_path / "consolidated_ws")
    monkeypatch.setenv("BUNDLEDIR", str(consolidated))
    load_state()
    from_consolidated = state_to_json()

    # Load split.
    _reset_loader(tmp_path / "split_ws")
    monkeypatch.setenv("BUNDLEDIR", str(split))
    load_state()
    from_split = state_to_json()

    assert from_consolidated == from_split
    assert set(from_consolidated["sites"]) == {"default", "extra"}
    assert set(from_split["sites"]) == {"default", "extra"}


def test_bundle_flat_files_merge_into_one_site(tmp_path: Path, monkeypatch) -> None:
    """the raw entities layout splits ONE site across per-entity files (no
    {sites} wrapper). Flat files must merge into a single default site, not
    fragment into a phantom site per file the server never activates."""
    for var in ("BUNDLEDIR", "INPUTDIR", "OUTPUTDIR", "BUNDLE_OUTPUT_DIR"):
        monkeypatch.delenv(var, raising=False)

    service_dir = tmp_path / "bundle" / "services" / "jira"
    service_dir.mkdir(parents=True)
    # Two halves of the SAME site: identity/users in one file, an extra user in
    # another. Both must land in the single active (default) site.
    (service_dir / "a_users.json").write_text(json.dumps(_flat_site("user-a")), encoding="utf-8")
    (service_dir / "b_users.json").write_text(
        json.dumps(
            {
                "users": {
                    "user-b": {
                        "accountId": "user-b",
                        "accountType": "atlassian",
                        "emailAddress": "user-b@example.com",
                        "displayName": "User B",
                        "active": True,
                        "timeZone": "America/New_York",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    _reset_loader(tmp_path / "flat_ws")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "bundle"))
    load_state()
    merged = state_to_json()

    # One site, and both per-file users present in it — nothing fragmented away.
    assert set(merged.get("sites", {"default": merged}).keys()) == {"default"} or "users" in merged
    state = merged["sites"]["default"] if "sites" in merged else merged
    assert "user-a" in state["users"]
    assert "user-b" in state["users"]
