"""Entity classes for triggers (step 5.1, ADR 0032).

The analysis writes short free entity types ("man", "backpack", "pickup truck"); a trigger's
`required_entities` uses four coarse classes. `entity_class` maps a type to its class by the
words in it, so "red backpack" is a portable_object and "pickup truck" a vehicle.
"""

import re
from typing import Literal

EntityClass = Literal["person", "vehicle", "portable_object", "animal"]
ENTITY_CLASSES: tuple[EntityClass, ...] = ("person", "vehicle", "portable_object", "animal")

# Checked in this order: the first class with a word in the type wins.
SYNONYMS: dict[EntityClass, frozenset[str]] = {
    "person": frozenset(
        [
            "person",
            "persons",
            "people",
            "man",
            "men",
            "woman",
            "women",
            "boy",
            "boys",
            "girl",
            "girls",
            "child",
            "children",
            "kid",
            "kids",
            "adult",
            "adults",
            "pedestrian",
            "pedestrians",
            "individual",
            "worker",
            "workers",
            "customer",
            "customers",
            "guard",
            "guards",
            "cyclist",
            "cyclists",
            "driver",
            "passenger",
            "visitor",
            "staff",
            "employee",
            "human",
        ]
    ),
    "vehicle": frozenset(
        [
            "vehicle",
            "vehicles",
            "car",
            "cars",
            "van",
            "vans",
            "truck",
            "trucks",
            "pickup",
            "bus",
            "buses",
            "motorcycle",
            "motorbike",
            "bicycle",
            "bike",
            "scooter",
            "suv",
            "taxi",
            "lorry",
            "trailer",
            "sedan",
            "jeep",
        ]
    ),
    "portable_object": frozenset(
        [
            "object",
            "objects",
            "item",
            "items",
            "bag",
            "bags",
            "backpack",
            "backpacks",
            "handbag",
            "purse",
            "suitcase",
            "luggage",
            "box",
            "boxes",
            "package",
            "packages",
            "parcel",
            "parcels",
            "briefcase",
            "case",
            "container",
            "bottle",
            "cup",
            "phone",
            "laptop",
            "umbrella",
            "trolley",
            "cart",
            "sack",
            "crate",
        ]
    ),
    "animal": frozenset(
        ["animal", "animals", "dog", "dogs", "cat", "cats", "bird", "birds", "horse", "horses"]
    ),
}

_WORD = re.compile(r"[a-z]+")


def entity_class(entity_type: str) -> EntityClass | None:
    """The class of a free entity type, or None when none of its words is known."""
    words = set(_WORD.findall(entity_type.lower()))
    for name in ENTITY_CLASSES:
        if words & SYNONYMS[name]:
            return name
    return None
