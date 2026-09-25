#!/usr/bin/env python3
"""
Utilities for setting up calendar data from JSON files.
Used by task preprocess scripts to initialize calendar state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def get_calendar_data_path(agent_workspace: str | Path) -> Path:
    """
    Get the calendar data file path for a given workspace.

    Stores in external_services/ directory NEXT TO the workspace:
    - If workspace is at /workspace/dumps/workspace, stores at /workspace/dumps/external_services/
    - Outside the agent workspace so it can't be read directly via filesystem MCP
    - Inside the dumps mount so it persists to the host
    - Path is deterministic and can be computed by preprocess, MCP, and evaluation
    """
    workspace_path = Path(agent_workspace)
    # Go up one level from workspace and create external_services directory
    external_services_dir = workspace_path.parent / "external_services"
    return external_services_dir / "calendar_data.json"


def load_events_from_json(json_path: Path) -> dict:
    """
    Load calendar data from a JSON file.

    Expected format:
    {
        "events": {
            "event-id": {
                "id": "event-id",
                "summary": "Event Title",
                "start": { "dateTime": "2025-12-10T11:30:00-05:00", "timeZone": "America/New_York" },
                "end": { "dateTime": "2025-12-10T12:30:00-05:00", "timeZone": "America/New_York" },
                "description": "Optional description",
                "location": "Optional location"
            }
        }
    }

    Args:
        json_path: Path to the JSON file

    Returns:
        Calendar data dictionary with events
    """
    with open(json_path) as f:
        data = json.load(f)

    # Ensure we have the events key
    if "events" not in data:
        data = {"events": data if isinstance(data, dict) else {}}

    # Add created/updated timestamps if missing
    now_utc = datetime.now(UTC).isoformat()
    for event_id, event in data["events"].items():
        if "id" not in event:
            event["id"] = event_id
        if "created" not in event:
            event["created"] = now_utc
        if "updated" not in event:
            event["updated"] = now_utc

    return data


def create_calendar_data(
    agent_workspace: str,
    json_path: Path,
    verbose: bool = True,
) -> Path:
    """
    Create calendar data from a JSON file and store in external_services.

    Uses a deterministic file path based on the workspace, so the MCP
    server can find it without needing a marker file in the agent workspace.

    Args:
        agent_workspace: Path to the agent's workspace directory
        json_path: Path to the JSON file with events (google_calendar.json)
        verbose: Print progress messages

    Returns:
        Path to the created data file
    """
    if not json_path.exists():
        if verbose:
            print(f"⚠️ No {json_path.name} found at {json_path}, creating empty calendar")
        calendar_data = {"events": {}}
    else:
        calendar_data = load_events_from_json(json_path)
        if verbose:
            print(f"✅ Loaded {len(calendar_data['events'])} events from {json_path}")

    # Deterministic path based on workspace (allows parallel runs)
    data_path = get_calendar_data_path(agent_workspace)

    # Create parent directory if needed
    data_path.parent.mkdir(parents=True, exist_ok=True)

    # Write calendar data to external_services directory (outside agent workspace)
    with open(data_path, "w") as f:
        json.dump(calendar_data, f, indent=2)
    if verbose:
        print(f"✅ Created {data_path} with {len(calendar_data['events'])} events")

    # Print summary
    if verbose:
        for event in calendar_data["events"].values():
            start_time = event.get("start", {}).get("dateTime", "unknown")
            print(f"   - {event.get('summary', 'Untitled')}: {start_time}")

    return data_path
