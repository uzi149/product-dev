"""Camera names and time phrases taken out of a query (step 4.1). No database or models."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from services.query_engine.filters import CameraRef, apply_explicit, parse_query

KARACHI = ZoneInfo("Asia/Karachi")  # UTC+5, no daylight saving
# Thursday 29 October 2026, 10:00 in Karachi (05:00 UTC).
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=KARACHI)

CAM02 = CameraRef(uuid4(), "CAM-02")
SIM01 = CameraRef(uuid4(), "SIM CAM-01")
FRONT = CameraRef(uuid4(), "Front door")
DOOR = CameraRef(uuid4(), "Door")
CAMERAS = [CAM02, SIM01, FRONT, DOOR]


def local(day: int, hour: int = 0, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KARACHI)


def parse(text: str, now: datetime = NOW, tz=KARACHI, **kw):
    return parse_query(text, CAMERAS, now, tz, **kw)


@pytest.mark.parametrize(
    ("text", "search", "start", "end", "label"),
    [
        ("a person falls today", "a person falls", local(29), NOW, "Today"),
        ("cars yesterday", "cars", local(28), local(29), "Yesterday"),
        ("a bag this morning", "a bag", local(29, 6), NOW, "This morning"),
        ("cars yesterday afternoon", "cars", local(28, 12), local(28, 18), "Yesterday afternoon"),
        ("a dog yesterday evening", "a dog", local(28, 18), local(29), "Yesterday evening"),
        ("trucks last night", "trucks", local(28, 18), local(29, 6), "Last night"),
        ("cars in the last 2 hours", "cars", local(29, 8), NOW, "In the last 2 hours"),
        ("cars last 2h", "cars", local(29, 8), NOW, "Last 2h"),
        ("cars past 30 min", "cars", local(29, 9, 30), NOW, "Past 30 min"),
        ("someone runs the last hour", "someone runs", local(29, 9), NOW, "The last hour"),
        ("people in the past 3 days", "people", local(26, 10), NOW, "In the past 3 days"),
        ("a van since 9am", "a van", local(29, 9), NOW, "Since 9am"),
        ("a van since 08:30", "a van", local(29, 8, 30), NOW, "Since 08:30"),
        ("a van since yesterday", "a van", local(28), NOW, "Since yesterday"),
        (
            "a car between 2pm and 3:30pm yesterday",
            "a car",
            local(28, 14),
            local(28, 15, 30),
            "Between 2pm and 3:30pm yesterday",
        ),
        ("a car from 22:00 to 01:00 yesterday", "a car", local(28, 22), local(29, 1), None),
        ("someone at 14:20 yesterday", "someone", local(28, 13, 50), local(28, 14, 50), None),
        ("someone around noon yesterday", "someone", local(28, 11, 30), local(28, 12, 30), None),
        ("a bike on 2026-03-07", "a bike", local(7, month=3), local(8, month=3), "On 2026-03-07"),
        ("a bike on 7 March", "a bike", local(7, month=3), local(8, month=3), "On 7 March"),
        ("a bike March 7th", "a bike", local(7, month=3), local(8, month=3), "March 7th"),
        ("a bike on the 7th of March", "a bike", local(7, month=3), local(8, month=3), None),
        (
            "a bike on 7 Mar in the morning",
            "a bike",
            local(7, 6, month=3),
            local(7, 12, month=3),
            None,
        ),
        ("people on Monday", "people", local(26), local(27), "On Monday"),
        ("people last Thursday", "people", local(22), local(23), "Last Thursday"),
        ("people on Thursday", "people", local(29), NOW, "On Thursday"),  # today
        ("someone around 14:20 on Monday", "someone", local(26, 13, 50), local(26, 14, 50), None),
    ],
)
def test_time_phrases(text, search, start, end, label):
    parsed = parse(text)
    assert parsed.search_text == search
    assert (parsed.start, parsed.end) == (start.astimezone(UTC), end.astimezone(UTC))
    if label is not None:
        assert [(u.kind, u.label) for u in parsed.understood] == [("time", label)]


def test_a_date_without_a_year_is_the_latest_one_not_in_the_future():
    parsed = parse("a bike on 3 December")  # December 2026 is still to come
    assert parsed.start == datetime(2025, 12, 3, tzinfo=KARACHI).astimezone(UTC)


def test_ranges_end_now_and_times_are_in_utc():
    parsed = parse("cars today")
    assert parsed.end == NOW.astimezone(UTC) and parsed.end.tzinfo is UTC


def test_the_time_zone_decides_where_a_day_starts():
    # 01:00 on the 29th in Karachi is still the 28th in UTC: "yesterday" differs.
    now = datetime(2026, 10, 29, 1, 0, tzinfo=KARACHI)
    in_karachi = parse("cars yesterday", now=now)
    in_utc = parse("cars yesterday", now=now, tz=UTC)
    assert in_karachi.start == local(28).astimezone(UTC)
    assert in_utc.start == datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "search", "cameras"),
    [
        ("someone left a bag on CAM-02", "someone left a bag", [CAM02]),
        ("cars on cam 2", "cars", [CAM02]),
        ("cars on cam02", "cars", [CAM02]),
        ("cars on sim cam 1", "cars", [SIM01]),
        ("cars on CAM-02 or SIM CAM-01", "cars", [CAM02, SIM01]),
        ("a person at the front door", "a person", [FRONT]),  # longest name first, not "Door"
        ("a person at the door", "a person", [DOOR]),
        ("someone in the doorway", "someone in the doorway", []),  # whole words only
        ("cars on CAM-09", "cars on CAM-09", []),  # no such camera: left as text
    ],
)
def test_camera_names(text, search, cameras):
    parsed = parse(text)
    assert parsed.search_text == search
    assert parsed.camera_ids == [c.id for c in cameras]
    assert [u.label for u in parsed.understood] == [c.name for c in cameras]


def test_cameras_and_times_together():
    parsed = parse("someone left a bag on CAM-02 yesterday between 14:00 and 15:00")
    assert parsed.search_text == "someone left a bag"
    assert parsed.camera_ids == [CAM02.id]
    assert (parsed.start, parsed.end) == (
        local(28, 14).astimezone(UTC),
        local(28, 15).astimezone(UTC),
    )
    assert [u.kind for u in parsed.understood] == ["camera", "time"]


@pytest.mark.parametrize(
    "text",
    ["a man in a red shirt", "the last car", "2 people", "at 25:00 a car", "on 31 February"],
)
def test_text_without_filters_is_left_alone(text):
    parsed = parse(text)
    assert parsed.search_text == text
    assert (parsed.camera_ids, parsed.start, parsed.end, parsed.understood) == ([], None, None, [])


def test_only_filters_leaves_no_search_text():
    assert parse("CAM-02 yesterday").search_text == ""


def test_parsing_can_be_switched_off_per_kind():
    no_cameras = parse("cars on CAM-02 yesterday", parse_cameras=False)
    assert no_cameras.search_text == "cars on CAM-02" and no_cameras.camera_ids == []
    assert no_cameras.start is not None
    no_times = parse("cars on CAM-02 yesterday", parse_times=False)
    assert no_times.search_text == "cars yesterday" and no_times.start is None


def test_explicit_filters_win():
    parsed = parse("cars on CAM-02 yesterday")
    chosen = [SIM01.id]
    start = datetime(2026, 10, 1, tzinfo=UTC)

    cameras_chosen = apply_explicit(parsed, camera_ids=chosen)
    assert cameras_chosen.camera_ids == chosen
    assert [u.kind for u in cameras_chosen.understood] == ["time"]
    assert cameras_chosen.start == parsed.start

    # A chosen start replaces the whole time phrase, end included.
    time_chosen = apply_explicit(parsed, start=start)
    assert (time_chosen.start, time_chosen.end) == (start, None)
    assert [u.kind for u in time_chosen.understood] == ["camera"]

    nothing_chosen = apply_explicit(parsed)
    assert nothing_chosen == parsed
    assert parsed.camera_ids == [CAM02.id]  # the original is not changed


def test_nothing_reaches_into_the_future():
    parsed = parse("cars this evening")  # 18:00-24:00 today, but it's 10:00
    assert parsed.start > parsed.end  # an empty range: nothing can match
    assert parsed.end == NOW.astimezone(UTC)
    assert parse("cars in the last 2 hours").end - parse("cars in the last 2 hours").start == (
        timedelta(hours=2)
    )
