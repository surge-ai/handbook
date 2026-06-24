from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.server import search_messages


def setup_function() -> None:
    seed_state(
        {
            "channels": {
                "C001": {"id": "C001", "name": "general", "is_private": False},
                "C002": {"id": "C002", "name": "engineering", "is_private": False},
            },
            "messages": {
                "C001": [
                    {
                        "ts": "1700000001.000",
                        "text": "Hello world",
                        "user": "U001",
                        "type": "message",
                        "reactions": [{"name": "wave", "users": ["U002"], "count": 1}],
                        "permalink": "https://example.com/message/C001/1700000001.000",
                    },
                    {
                        "ts": "1700000002.000",
                        "text": "Reply in thread",
                        "user": "U002",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                    {
                        "ts": "1700000003.000",
                        "text": "Another message",
                        "user": "U001",
                        "type": "message",
                        "is_starred": True,
                    },
                    {"ts": "1700000004.000", "text": "Deployment is ready", "user": "U002", "type": "message"},
                    {
                        "ts": "1700000004.500",
                        "text": "Literal from:alice appears in this message",
                        "user": "U002",
                        "type": "message",
                    },
                ],
                "C002": [
                    {"ts": "1700000005.000", "text": "Hello from engineering", "user": "U002", "type": "message"},
                    {
                        "ts": "1700000006.000",
                        "text": "Code review needed",
                        "user": "U001",
                        "type": "message",
                        "pinned_to": ["C002"],
                    },
                    {
                        "ts": "1700000007.000",
                        "text": "Design handoff link https://example.com/spec",
                        "user": "U003",
                        "type": "message",
                    },
                    {
                        "ts": "1700000008.000",
                        "text": "Multi marker message",
                        "user": "U001",
                        "type": "message",
                        "is_starred": True,
                        "reactions": [{"name": "eyes", "users": ["U002"], "count": 1}],
                    },
                    {
                        "ts": "1700000009.000",
                        "text": "Ambiguous display marker",
                        "user": "U004",
                        "type": "message",
                    },
                ],
            },
            "users": {
                "U001": {
                    "id": "U001",
                    "name": "alice",
                    "real_name": "Alice Smith",
                    "profile": {"display_name": "alice"},
                },
                "U002": {"id": "U002", "name": "bob", "real_name": "Bob Jones", "profile": {"display_name": "bob"}},
                "U003": {
                    "id": "U003",
                    "name": "alicia.smith",
                    "real_name": "Alicia Smith",
                    "profile": {"display_name": "alicia.smith"},
                },
                "U004": {
                    "id": "U004",
                    "name": "charlie",
                    "real_name": "Charles Example",
                    "profile": {"display_name": "bob"},
                },
            },
        }
    )


def texts(result: dict) -> list[str]:
    return [match["text"] for match in result["messages"]["matches"]]


@pytest.mark.asyncio
async def test_text_matching_scoping_sorting_and_context() -> None:
    result = await search_messages("Hello")
    assert result["ok"] is True
    assert result["messages"]["total"] == 2
    assert all("hello" in match["text"].lower() for match in result["messages"]["matches"])
    assert {match["channel"]["id"] for match in result["messages"]["matches"]} == {"C001", "C002"}
    assert [float(match["ts"]) for match in result["messages"]["matches"]] == sorted(
        [float(match["ts"]) for match in result["messages"]["matches"]], reverse=True
    )
    assert all(match["channel"]["name"] for match in result["messages"]["matches"])
    assert all(match["user_name"] != "Unknown" for match in result["messages"]["matches"])
    assert all("username" in match and "display_name" in match for match in result["messages"]["matches"])

    scoped = await search_messages("Hello", channel_id="C001")
    assert scoped["messages"]["total"] == 1
    assert scoped["messages"]["matches"][0]["channel"]["id"] == "C001"
    assert (await search_messages("Hello", channel_id="INVALID"))["error"] == "channel_not_found"
    assert (await search_messages("zzzznonexistent"))["messages"]["matches"] == []


@pytest.mark.asyncio
async def test_user_and_thread_matching() -> None:
    by_user_name = await search_messages("alice smith")
    assert by_user_name["messages"]["total"] > 0
    assert all(match["user"] == "U001" for match in by_user_name["messages"]["matches"])

    reply = await search_messages("Reply in thread")
    assert reply["messages"]["total"] == 1
    assert reply["messages"]["matches"][0]["thread_ts"] == "1700000001.000"


@pytest.mark.asyncio
async def test_pagination_limits_and_cursors() -> None:
    first = await search_messages("Hello", limit=1)
    assert first["messages"]["total"] == 2
    assert first["response_metadata"]["next_cursor"]
    second = await search_messages("Hello", limit=1, cursor=first["response_metadata"]["next_cursor"])
    assert second["messages"]["matches"][0]["ts"] != first["messages"]["matches"][0]["ts"]

    capped = await search_messages("message", limit=999)
    assert "limit exceeds the maximum of 100; using 100." in capped["response_metadata"]["warnings"]

    malformed = await search_messages("hello", limit=1, cursor="not-a-number")
    assert malformed["messages"]["matches"][0]["text"] == "Hello from engineering"
    assert "Invalid cursor 'not-a-number'; using the first page." in malformed["response_metadata"]["warnings"]


@pytest.mark.asyncio
async def test_word_and_phrase_semantics() -> None:
    assert (await search_messages("world hello"))["messages"]["matches"][0]["text"] == "Hello world"
    assert (await search_messages("hello goodbye"))["messages"]["total"] == 0
    assert (await search_messages('"hello world"'))["messages"]["matches"][0]["text"] == "Hello world"
    assert (await search_messages('"world hello"'))["messages"]["total"] == 0
    assert (await search_messages('needed "code review"'))["messages"]["matches"][0]["text"] == "Code review needed"
    quoted = await search_messages('"from:alice"')
    assert quoted["messages"]["matches"][0]["text"] == "Literal from:alice appears in this message"
    assert quoted["response_metadata"]["warnings"] == []
    mixed = await search_messages("deployment bob")
    assert mixed["messages"]["matches"][0]["text"] == "Deployment is ready"
    assert mixed["messages"]["matches"][0]["user"] == "U002"


@pytest.mark.asyncio
async def test_channel_and_user_filters() -> None:
    assert (await search_messages("in:#engineering hello"))["messages"]["matches"][0]["channel"]["id"] == "C002"
    assert (await search_messages("in:C001 hello"))["messages"]["matches"][0]["channel"]["id"] == "C001"
    assert (await search_messages("in:#engineering hello", channel_id="C001"))["error"] == "channel_scope_conflict"
    assert (await search_messages("in:#nonexistent hello"))["messages"]["total"] == 0
    assert (await search_messages("in:#engineering hello", channel_id="INVALID"))["error"] == "channel_not_found"

    assert (await search_messages("from:@bob deployment"))["messages"]["matches"][0]["user"] == "U002"
    assert (await search_messages("from:U001 review"))["messages"]["matches"][0]["text"] == "Code review needed"
    assert (await search_messages("from:alicia link"))["messages"]["matches"][0]["user"] == "U003"
    assert {match["user"] for match in (await search_messages("from:@bob"))["messages"]["matches"]} == {"U002"}
    display_name_match = (await search_messages("from:bob ambiguous"))["messages"]["matches"][0]
    assert display_name_match["user"] == "U004"
    assert display_name_match["username"] == "charlie"
    assert display_name_match["display_name"] == "bob"
    from_me = await search_messages("from:me hello")
    assert from_me["messages"]["total"] == 0
    assert (
        "from:me is unsupported because caller identity is not available; it will match no users."
        in from_me["response_metadata"]["warnings"]
    )


@pytest.mark.asyncio
async def test_date_filters() -> None:
    result = await search_messages("hello after:1700000002 before:1700000006")
    assert result["messages"]["matches"][0]["text"] == "Hello from engineering"
    assert (await search_messages("hello before:1700000005"))["messages"]["matches"][0]["text"] == "Hello world"
    assert (await search_messages("during:2023-11-14 hello"))["messages"]["total"] == 2
    assert (await search_messages("during:2023-11 hello"))["messages"]["total"] == 2
    assert (await search_messages("during:2023 hello"))["messages"]["total"] == 2
    invalid = await search_messages("during:2023-Q1 hello")
    assert invalid["messages"]["total"] == 2
    assert (
        "Invalid during: date '2023-Q1'. Use YYYY, YYYY-MM, YYYY-MM-DD, or a parseable date."
        in invalid["response_metadata"]["warnings"]
    )


@pytest.mark.asyncio
async def test_has_filters_and_empty_filters() -> None:
    unsupported = await search_messages("has:calendar hello")
    assert unsupported["messages"]["total"] == 0
    assert (
        "Unsupported has: value 'calendar'. Supported values: link, reaction, star, pin."
        in unsupported["response_metadata"]["warnings"]
    )
    assert (await search_messages("has:link"))["messages"]["matches"][0][
        "text"
    ] == "Design handoff link https://example.com/spec"
    assert (await search_messages('has:link "hello world"'))["messages"]["total"] == 0
    assert set(texts(await search_messages("has:reaction"))) == {"Hello world", "Multi marker message"}
    assert "Another message" in texts(await search_messages("has:star"))
    assert (await search_messages("has:pin"))["messages"]["matches"][0]["text"] == "Code review needed"
    assert (await search_messages("has:reaction has:star"))["messages"]["matches"][0]["text"] == "Multi marker message"
    assert (await search_messages("has:reaction has:pin"))["messages"]["total"] == 0

    with_text = await search_messages("from: hello")
    assert with_text["messages"]["total"] == 2
    assert "Empty from: filter was ignored." in with_text["response_metadata"]["warnings"]
    assert (await search_messages("from:"))["error"] == "missing_query"
    assert "Empty in: filter was ignored." in (await search_messages("in:"))["response_metadata"]["warnings"]
    assert "Empty has: filter was ignored." in (await search_messages("has:"))["response_metadata"]["warnings"]

    combined = await search_messages("in:#general from:@alice has:reaction after:2023-11-14 hello")
    assert combined["messages"]["total"] == 1
    assert combined["messages"]["matches"][0]["text"] == "Hello world"
