from __future__ import annotations

import pytest
from jira_mock.server import create_issue, get_project_issues, list_sites, search, update_issue
from jira_mock.state import get_active_site_id, state_from_json, state_to_json


@pytest.mark.asyncio
async def test_site_selector_routes_reads_and_writes_independently() -> None:
    state_from_json({"sites": {"default": {}, "acme": {}}})

    default_issue = await create_issue("MOCK", "Default site issue", "Task", site_id="default")
    acme_issue = await create_issue("MOCK", "Acme site issue", "Task", site_id="acme")

    assert default_issue["key"] == "MOCK-1"
    assert acme_issue["key"] == "MOCK-1"

    listed = await list_sites()
    assert listed["status"] == "success"
    assert listed["total"] == 2
    assert {site["site_id"] for site in listed["sites"]} == {"default", "acme"}

    default_issues = await get_project_issues("MOCK", site_id="default")
    acme_issues = await get_project_issues("MOCK", site_id="acme")
    assert default_issues["issues"][0]["fields"]["summary"] == "Default site issue"
    assert acme_issues["issues"][0]["fields"]["summary"] == "Acme site issue"

    assert (await search('summary ~ "Acme"', site_id="acme"))["total"] == 1
    assert (await search('summary ~ "Acme"', site_id="default"))["total"] == 0

    exported = state_to_json()
    assert set(exported["sites"]) == {"default", "acme"}
    assert exported["sites"]["default"]["issues"]["MOCK-1"]["fields"]["summary"] == "Default site issue"
    assert exported["sites"]["acme"]["issues"]["MOCK-1"]["fields"]["summary"] == "Acme site issue"


@pytest.mark.asyncio
async def test_failed_site_write_does_not_mutate_other_sites() -> None:
    state_from_json({"sites": {"default": {}, "acme": {}}})
    await create_issue("MOCK", "Default site issue", "Task", site_id="default")
    before = state_to_json()

    with pytest.raises(ValueError, match="Issue MISSING-1 not found"):
        await update_issue("MISSING-1", "{}", site_id="acme")

    assert state_to_json() == before


@pytest.mark.asyncio
async def test_state_import_resets_active_site_to_default() -> None:
    state_from_json({"sites": {"default": {}, "acme": {}}})
    await get_project_issues("MOCK", site_id="acme")
    assert get_active_site_id() == "acme"

    state_from_json({"sites": {"default": {}}})

    assert get_active_site_id() == "default"
    assert (await get_project_issues("MOCK"))["total"] == 0
