"""State handlers for Google Mail tools."""

from __future__ import annotations

from google_mail.models import GoogleMailState
from google_mail.state import export_state_model, state_from_json


async def export_state() -> GoogleMailState:
    return export_state_model()


async def import_state(state: GoogleMailState) -> dict:
    state_from_json(state)
    return {"ok": True}
