"""Words in a query or a trigger rule that name an action of the vocabulary (step 4.2).

    query_actions("someone rings the doorbell")      -> {"ring_doorbell"}
    query_actions("a pickup truck parks")            -> {"vehicle_park"}
    query_actions("a person left a bag behind")      -> {"leave", "leave_object_behind"}

Plain word patterns, no model. Arriving, leaving and parking mean the vehicle actions when the
text mentions a vehicle, otherwise the person ones. Shared with the trigger compiler (Phase 5).
"""

import re

from shared.schemas.observation import ACTION_VOCABULARY

_VEHICLE = re.compile(r"\b(cars?|trucks?|vans?|vehicles?|bus|buses|lorr(?:y|ies)|suvs?|taxis?)\b")
_OBJECT = r"(?:bag|backpack|box|package|parcel|object|item|suitcase|luggage|something|it)"

# Action -> patterns, matched on the lowercased text.
_ALWAYS: dict[str, list[str]] = {
    "walk": [r"\bwalk(?:s|ed|ing)?\b", r"\bstroll"],
    "run": [r"\brun(?:s|ning)?\b", r"\bran\b", r"\bjog(?:s|ged|ging)?\b", r"\bsprint"],
    "stand": [r"\bstand(?:s|ing)?\b", r"\bstood\b"],
    "sit": [r"\bsit(?:s|ting)?\b", r"\bsat\b", r"\bseated\b"],
    "loiter": [
        r"\bloiter",
        r"\bhang(?:s|ing)? around\b",
        r"\bhung around\b",
        r"\bling(?:er|ers|ered|ering)\b",
    ],
    "place_object": [
        r"\b(?:place[sd]?|placing)\b",
        r"\b(?:put|puts|putting|set|sets|setting)\b.{0,20}\bdown\b",
        r"\bdrop(?:s|ped|ping)?\b(?:.{0,20}\boff\b)?",
        r"\bunload(?:s|ed|ing)?\b",
    ],
    "pick_up_object": [
        r"\bpick(?:s|ed|ing)?\b.{0,20}\bup\b",
        r"\bgrab(?:s|bed|bing)?\b",
        r"\b(?:take|takes|took|taking)\b.{0,30}\b(?:out|from|away)\b",
        r"\bunload(?:s|ed|ing)?\b",
    ],
    "carry_object": [r"\bcarr(?:y|ies|ied|ying)\b", r"\bhold(?:s|ing)?\b", r"\bheld\b"],
    "leave_object_behind": [
        rf"\b(?:leave|leaves|left|leaving|abandon(?:s|ed|ing)?)\b.{{0,25}}\b{_OBJECT}\b",
        r"\bleft\b.{0,20}\bbehind\b",
        r"\bwithout (?:it|the|his|her|their)\b",
        r"\bunattended\b",
    ],
    "interact_with_person": [
        r"\b(?:talk|speak|chat)(?:s|ed|ing)?\b",
        r"\bspoke\b",
        r"\bmeet(?:s|ing)?\b",
        r"\bmet\b",
        r"\bshak(?:e|es|ing) hands\b",
        r"\bhug(?:s|ged|ging)?\b",
        r"\binteract",
        r"\bargu(?:e|es|ed|ing)\b",
    ],
    "fall": [r"\bfall(?:s|en|ing)?\b", r"\bfell\b", r"\btrip(?:s|ped)?\b", r"\bcollaps"],
    "open_door": [r"\bopen(?:s|ed|ing)?\b.{0,15}\bdoor\b"],
    "close_door": [r"\b(?:close[sd]?|closing|shut(?:s|ting)?)\b.{0,15}\bdoor\b"],
    "ring_doorbell": [
        r"\bdoorbell\b",
        r"\b(?:ring|rings|rang|ringing|press(?:es|ed)?)\b.{0,15}\bbell\b",
    ],
}
# Person action / vehicle action for the same words.
_ARRIVE = [r"\barriv(?:e|es|ed|ing)\b", r"\bcom(?:e|es|ing) in\b", r"\bcame in\b"]
_LEAVE = [
    r"\bleav(?:e|es|ing)\b",
    r"\bleft\b",
    r"\bdepart",
    r"\bexit(?:s|ed|ing)?\b",
    r"\bgo(?:es)? out\b",
    r"\bwent out\b",
]
_PERSON_ONLY: dict[str, list[str]] = {
    "enter": [r"\benter(?:s|ed|ing)?\b", r"\bwalk(?:s|ed|ing)? in(?:to)?\b", *_ARRIVE],
    "leave": [*_LEAVE, r"\bwalk(?:s|ed|ing)? (?:out|away)\b"],
}
_VEHICLE_ONLY: dict[str, list[str]] = {
    "vehicle_arrive": [
        *_ARRIVE,
        r"\benter(?:s|ed|ing)?\b",
        r"\bpull(?:s|ed|ing)? in",
        r"\bdr(?:ive|ives|ove|iving) in",
    ],
    "vehicle_depart": [
        *_LEAVE,
        r"\bdr(?:ive|ives|ove|iving) (?:off|away|out)\b",
        r"\bpull(?:s|ed|ing)? (?:out|away)\b",
    ],
    "vehicle_park": [r"\bpark(?:s|ed)?\b", r"\bparking\b(?! (?:lot|space|area|garage))"],
}


def _compile(table: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    unknown = set(table) - set(ACTION_VOCABULARY)
    assert not unknown, f"not in the action vocabulary: {unknown}"
    return {action: [re.compile(p) for p in patterns] for action, patterns in table.items()}


ALWAYS = _compile(_ALWAYS)
PERSON_ONLY = _compile(_PERSON_ONLY)
VEHICLE_ONLY = _compile(_VEHICLE_ONLY)


def query_actions(text: str) -> set[str]:
    """The vocabulary actions the text names."""
    text = text.lower()
    tables = [ALWAYS, VEHICLE_ONLY if _VEHICLE.search(text) else PERSON_ONLY]
    return {
        action
        for table in tables
        for action, patterns in table.items()
        if any(p.search(text) for p in patterns)
    }
