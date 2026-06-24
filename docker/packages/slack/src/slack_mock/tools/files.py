"""File tool handlers."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from slack_mock.models import SlackFile, SlackFileMode, SlackMessage, SlackMessageSubtype, SlackMessageType
from slack_mock.state import add_message, all_files, generate_file_id, generate_timestamp, get_bot_user_id, get_channel
from slack_mock.tools.common import model_dump, now_seconds

_MIME_MAP = {
    ".png": ("image/png", "png", "PNG"),
    ".jpg": ("image/jpeg", "jpg", "JPEG"),
    ".jpeg": ("image/jpeg", "jpeg", "JPEG"),
    ".gif": ("image/gif", "gif", "GIF"),
    ".pdf": ("application/pdf", "pdf", "PDF"),
    ".txt": ("text/plain", "text", "Plain Text"),
    ".csv": ("text/csv", "csv", "CSV"),
    ".json": ("application/json", "javascript", "JSON"),
    ".zip": ("application/zip", "zip", "Zip"),
    ".doc": ("application/msword", "doc", "Word Document"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx", "Word Document"),
    ".xls": ("application/vnd.ms-excel", "xls", "Excel Spreadsheet"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", "Excel Spreadsheet"),
}


def upload_file(
    channel_id: str,
    filename: str,
    content_base64: str,
    title: str | None = None,
    initial_comment: str | None = None,
) -> dict[str, Any]:
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found", "file": {}}
    try:
        size = len(base64.b64decode(content_base64, validate=True))
    except Exception:
        return {"ok": False, "error": "invalid_base64", "file": {}}
    file_id = generate_file_id()
    now = now_seconds()
    ext = Path(filename).suffix.lower()
    mimetype, filetype, pretty_type = _MIME_MAP.get(
        ext, (mimetypes.guess_type(filename)[0] or "application/octet-stream", "binary", "Binary")
    )
    file = SlackFile(
        id=file_id,
        created=now,
        timestamp=now,
        name=filename,
        title=title or filename,
        mimetype=mimetype,
        filetype=filetype,
        pretty_type=pretty_type,
        user=get_bot_user_id(),
        size=size,
        mode=SlackFileMode.HOSTED,
        is_external=False,
        is_public=True,
        url_private=f"https://files.slack.com/files-pri/{file_id}/{filename}",
        url_private_download=f"https://files.slack.com/files-pri/{file_id}/download/{filename}",
    )
    ts = generate_timestamp()
    message = SlackMessage(
        type=SlackMessageType.MESSAGE,
        subtype=SlackMessageSubtype.FILE_SHARE,
        user=get_bot_user_id(),
        text=initial_comment or f"uploaded a file: {title or filename}",
        ts=ts,
        team=channel.context_team_id,
        files=[file],
        upload=True,
    )
    add_message(channel_id, message)
    return {"ok": True, "file": model_dump(file)}


def list_files(channel_id: str, limit: int = 20) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found", "files": [], "total": 0}
    files = sorted(all_files(channel_id), key=lambda file: file.created or 0, reverse=True)
    return {"ok": True, "files": model_dump(files[: limit or 20]), "total": len(files)}
