"""Camera names and time phrases in a plain-English query, turned into filters (scope §10 step 1).

    "someone left a bag on CAM-02 yesterday afternoon"
      -> cameras [CAM-02], yesterday 12:00-18:00 in the operator's time zone,
         search text "someone left a bag"

Rule-based (MVP decision "Query filters"): a camera is any camera's display name, compared as
whole words in a normalised form ("cam 2" = "CAM-02"); times are the phrases below. What isn't
recognised stays in the search text. Filters chosen in the UI win over the text (apply_explicit).

Times: today, yesterday, tonight, last night, (this / yesterday / in the) morning, afternoon,
evening, last / past N minutes / hours / days, since <time or day>, between <time> and <time>,
from <time> to <time>, at / around <time> (30 min either side), on <date> (2026-03-07,
7 March, March 7th), on / last <weekday>. A time is 14:20, 9am, 2:30 pm, noon or midnight; a
bare number is never a time. Ranges end at "now" at the latest.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from uuid import UUID

PREPOSITIONS = {"on", "at", "from", "in", "or", "and", "camera", "cameras", "the"}
AROUND = timedelta(minutes=30)
DAY_PARTS = {"morning": (6, 12), "afternoon": (12, 18), "evening": (18, 24)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        1,
    )
    for name in names
}
UNITS = {"m": "minutes", "min": "minutes", "h": "hours", "hr": "hours", "d": "days"}

_CLOCK = r"(?:noon|midnight|\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))"
_DAY = r"(?:today|yesterday|" + "|".join(WEEKDAYS) + r")"
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
_UNIT = r"minutes?|mins?|m|hours?|hrs?|h|days?|d"

# Order matters: longer phrases are taken out before the shorter ones they contain.
PATTERNS = [
    ("since", rf"\bsince\s+({_CLOCK}|{_DAY})\b"),
    (
        "between",
        rf"\b(?:between\s+({_CLOCK})\s+and|from\s+({_CLOCK})\s+(?:to|until|till))\s+({_CLOCK})",
    ),
    ("around", rf"\b(?:at|around|about)\s+({_CLOCK})"),
    (
        "relative",
        rf"\b(?:in\s+)?(?:the\s+)?(?:last|past)\s+(?:(\d+)\s*)?({_UNIT})\b",
    ),
    ("last_night", r"\blast\s+night\b"),
    ("tonight", r"\btonight\b"),
    ("iso_date", r"\b(?:on\s+)?(\d{4})-(\d{2})-(\d{2})\b"),
    ("day_month", rf"\b(?:on\s+)?(?:the\s+)?{_ORDINAL}(?:\s+of)?\s+({_MONTH})\b{_YEAR}"),
    ("month_day", rf"\b(?:on\s+)?({_MONTH})\s+{_ORDINAL}\b{_YEAR}"),
    ("weekday", r"\b(?:on\s+)?(last\s+)?(" + "|".join(WEEKDAYS) + r")\b"),
    ("day", r"\b(today|yesterday)\b"),
    ("day_part", r"\b(?:in\s+the\s+|this\s+)?(morning|afternoon|evening)\b"),
]
_COMPILED = [(kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in PATTERNS]


@dataclass(frozen=True)
class CameraRef:
    id: UUID
    name: str


@dataclass
class Understood:
    """One thing taken from the text, for the UI's chips."""

    kind: str  # "camera" or "time"
    label: str  # "CAM-02", "Yesterday afternoon"
    camera_id: UUID | None = None


@dataclass
class ParsedQuery:
    search_text: str
    camera_ids: list[UUID] = field(default_factory=list)
    start: datetime | None = None  # UTC
    end: datetime | None = None
    understood: list[Understood] = field(default_factory=list)


def _words(text: str) -> list[tuple[str, int, int]]:
    """Normalised words with their spans: lowercase, letters and digits split apart ("cam02" ->
    "cam", "2"), numbers without leading zeros."""
    words = []
    for match in re.finditer(r"[A-Za-z]+|\d+", text):
        word = match.group().lower()
        if word.isdigit():
            word = str(int(word))
        words.append((word, match.start(), match.end()))
    return words


def _take_cameras(text: str, cameras: list[CameraRef]) -> tuple[str, list[CameraRef]]:
    """Remove every camera name (whole words, longest first) and a preposition before it.
    The cameras come back in the order the query names them."""
    found: list[CameraRef] = []
    original = [w for w, _, _ in _words(text)]
    by_length = sorted(cameras, key=lambda c: len(_words(c.name)), reverse=True)
    for camera in by_length:
        name = [w for w, _, _ in _words(camera.name)]
        if not name:
            continue
        while True:
            words = _words(text)
            plain = [w for w, _, _ in words]
            at = next(
                (i for i in range(len(plain) - len(name) + 1) if plain[i : i + len(name)] == name),
                None,
            )
            if at is None:
                break
            first = at
            while first > 0 and plain[first - 1] in PREPOSITIONS and at - first < 2:
                first -= 1
            start, end = words[first][1], words[at + len(name) - 1][2]
            text = f"{text[:start]} {text[end:]}"
            if camera not in found:
                found.append(camera)

    def position(camera: CameraRef) -> int:
        name = [w for w, _, _ in _words(camera.name)]
        return next(
            (i for i in range(len(original)) if original[i : i + len(name)] == name), len(original)
        )

    return text, sorted(found, key=position)


def _clock(text: str) -> time:
    text = text.lower().replace(" ", "")
    if text == "noon":
        return time(12)
    if text == "midnight":
        return time(0)
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", text)
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"not a time: {text}")
    return time(hour, minute)


def _past_date(today: date, month: int, day: int, year: str | None) -> date:
    """That date; without a year, the latest one that isn't in the future."""
    if year:
        return date(int(year), month, day)
    candidate = date(today.year, month, day)
    return candidate if candidate <= today else date(today.year - 1, month, day)


def _weekday(today: date, name: str, strictly_before: bool) -> date:
    back = (today.weekday() - WEEKDAYS.index(name)) % 7
    if back == 0 and strictly_before:
        back = 7
    return today - timedelta(days=back)


def _day_of(word: str, today: date) -> date:
    word = word.lower()
    if word == "today":
        return today
    if word == "yesterday":
        return today - timedelta(days=1)
    return _weekday(today, word, strictly_before=False)


@dataclass
class _Found:
    day: date | None = None
    day_part: tuple[int, int] | None = None
    between: tuple[time, time] | None = None
    around: time | None = None
    since_time: time | None = None
    since_day: date | None = None
    relative: timedelta | None = None
    last_night: bool = False


def _take_times(text: str, today: date) -> tuple[str, _Found, list[tuple[int, str]]]:
    """Remove the time phrases; returns the text, what they say, and (position, phrase)."""
    found = _Found()
    phrases: list[tuple[int, str]] = []
    for kind, pattern in _COMPILED:
        while (match := pattern.search(text)) is not None:
            groups = match.groups()
            try:
                if kind == "since":
                    if re.fullmatch(_DAY, groups[0], re.IGNORECASE):
                        found.since_day = _day_of(groups[0], today)
                    else:
                        found.since_time = _clock(groups[0])
                elif kind == "between":
                    found.between = (_clock(groups[0] or groups[1]), _clock(groups[2]))
                elif kind == "around":
                    found.around = _clock(groups[0])
                elif kind == "relative":
                    unit = groups[1].lower().rstrip("s")
                    unit = UNITS.get(unit, unit + "s")
                    found.relative = timedelta(**{unit: int(groups[0] or 1)})
                elif kind == "last_night":
                    found.last_night = True
                elif kind == "tonight":
                    found.day, found.day_part = today, DAY_PARTS["evening"]
                elif kind == "iso_date":
                    found.day = date(int(groups[0]), int(groups[1]), int(groups[2]))
                elif kind == "day_month":
                    found.day = _past_date(
                        today, MONTHS[groups[1].lower()], int(groups[0]), groups[2]
                    )
                elif kind == "month_day":
                    found.day = _past_date(
                        today, MONTHS[groups[0].lower()], int(groups[1]), groups[2]
                    )
                elif kind == "weekday":
                    found.day = _weekday(today, groups[1].lower(), strictly_before=bool(groups[0]))
                elif kind == "day":
                    found.day = _day_of(groups[0], today)
                elif kind == "day_part":
                    found.day_part = DAY_PARTS[groups[0].lower()]
            except ValueError:  # 25:00, 30 February: not a time phrase after all
                break
            phrases.append((match.start(), match.group().strip()))
            text = f"{text[: match.start()]} {text[match.end() :]}"
    return text, found, phrases


def _resolve(found: _Found, now: datetime, tz: tzinfo) -> tuple[datetime, datetime] | None:
    """The time range the phrases describe, in UTC, ending at `now` at the latest."""
    today = now.astimezone(tz).date()

    def at(day: date, hours: float = 0, clock: time | None = None) -> datetime:
        moment = datetime.combine(day, clock or time(0), tzinfo=tz)
        return moment + timedelta(hours=hours)

    ranges: list[tuple[datetime, datetime]] = []
    day = found.day or today
    if found.last_night:
        ranges.append((at(today - timedelta(days=1), 18), at(today, 6)))
    if found.between:
        first, second = at(day, clock=found.between[0]), at(day, clock=found.between[1])
        ranges.append((first, second if second > first else second + timedelta(days=1)))
    elif found.around:
        moment = at(day, clock=found.around)
        ranges.append((moment - AROUND, moment + AROUND))
    elif found.day_part:
        ranges.append((at(day, found.day_part[0]), at(day, found.day_part[1])))
    elif found.day and not found.since_time:
        ranges.append((at(found.day), at(found.day + timedelta(days=1))))
    if found.since_time:
        ranges.append((at(day, clock=found.since_time), now))
    if found.since_day:
        ranges.append((at(found.since_day), now))
    if found.relative:
        ranges.append((now - found.relative, now))
    if not ranges:
        return None
    start = max(r[0] for r in ranges)
    end = min(min(r[1] for r in ranges), now)
    return start.astimezone(UTC), end.astimezone(UTC)


def _clean(text: str) -> str:
    """Spaces collapsed; prepositions and punctuation left dangling at either end removed."""
    words = re.sub(r"\s+", " ", text).strip(" ,;.-").split(" ")
    dangling = PREPOSITIONS | {"during", "between", "since"}
    while words and words[-1].lower().strip(",") in dangling:
        words.pop()
    return " ".join(words).strip(" ,;.-")


def parse_query(
    text: str,
    cameras: list[CameraRef],
    now: datetime,
    tz: tzinfo,
    *,
    parse_cameras: bool = True,
    parse_times: bool = True,
) -> ParsedQuery:
    """Take the camera names and time phrases out of `text`. `parse_cameras` / `parse_times`
    off leave those words in the search text (the UI's removed chips)."""
    understood: list[Understood] = []
    found_cameras: list[CameraRef] = []
    if parse_cameras:
        text, found_cameras = _take_cameras(text, cameras)
        understood += [Understood("camera", c.name, c.id) for c in found_cameras]
    start = end = None
    if parse_times:
        text, found, phrases = _take_times(text, now.astimezone(tz).date())
        span = _resolve(found, now, tz)
        if span is not None:
            start, end = span
            label = " ".join(p for _, p in sorted(phrases))
            understood.append(Understood("time", label[:1].upper() + label[1:]))
    return ParsedQuery(
        search_text=_clean(text),
        camera_ids=[c.id for c in found_cameras],
        start=start,
        end=end,
        understood=understood,
    )


def apply_explicit(
    parsed: ParsedQuery,
    camera_ids: list[UUID] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ParsedQuery:
    """Filters chosen in the UI win: chosen cameras replace the named ones, and a chosen start or
    end replaces the whole time phrase (a range is never half text, half form)."""
    understood = parsed.understood
    result = replace(parsed)
    if camera_ids:
        result.camera_ids = list(camera_ids)
        understood = [u for u in understood if u.kind != "camera"]
    if start is not None or end is not None:
        result.start, result.end = start, end
        understood = [u for u in understood if u.kind != "time"]
    result.understood = understood
    return result
