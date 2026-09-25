from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.server import list_files, upload_file


def setup_function() -> None:
    seed_state(
        {
            "channels": {"C001": {"id": "C001", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {"C001": []},
        }
    )


@pytest.mark.asyncio
async def test_upload_file_creates_message_and_detects_type() -> None:
    result = await upload_file("C001", "report.pdf", "AAAA")
    assert result["ok"] is True
    assert result["file"]["name"] == "report.pdf"
    assert result["file"]["mimetype"] == "application/pdf"
    assert result["file"]["id"]

    png = await upload_file("C001", "screenshot.png", "AA==")
    csv = await upload_file("C001", "data.csv", "AA==")
    assert png["file"]["mimetype"] == "image/png"
    assert png["file"]["filetype"] == "png"
    assert csv["file"]["mimetype"] == "text/csv"


@pytest.mark.asyncio
async def test_upload_file_title_comment_errors_and_size() -> None:
    titled = await upload_file("C001", "q1.xlsx", "AA==", title="Q1 Financial Report")
    assert titled["file"]["title"] == "Q1 Financial Report"

    await upload_file("C001", "doc.pdf", "AA==", initial_comment="Here is the spec document")
    files = await list_files("C001")
    assert files["total"] == 2

    missing = await upload_file("INVALID", "x.txt", "AA==")
    assert missing["ok"] is False
    assert missing["error"] == "channel_not_found"

    sized = await upload_file("C001", "test.txt", "SGVsbG8gV29ybGQ=")
    assert sized["file"]["size"] > 0


@pytest.mark.asyncio
async def test_list_files_empty_uploaded_limit_and_errors() -> None:
    assert await list_files("C001") == {"ok": True, "files": [], "total": 0}
    await upload_file("C001", "a.png", "AA==")
    await upload_file("C001", "b.pdf", "AA==")
    result = await list_files("C001")
    assert result["total"] == 2
    assert {file["name"] for file in result["files"]} == {"a.png", "b.pdf"}

    await upload_file("C001", "c.png", "AA==")
    limited = await list_files("C001", limit=2)
    assert len(limited["files"]) == 2
    assert limited["total"] == 3
    assert (await list_files("INVALID"))["error"] == "channel_not_found"
