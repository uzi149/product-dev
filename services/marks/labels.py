"""The words the VLM is asked to find in a clip (step 5.7).

A trigger's compiled rule names coarse classes (person, vehicle, portable_object, animal; ADR
0032) and its text often the exact thing ("a bag", "a red car"); a question only has its text.
Grounding works best with plain nouns, so: "person" for every kind of person (asking for "man"
would leave women out), the vehicle or object word the text uses, and a default word for a class
the text doesn't name. With no class at all, the open prompt asks for every distinct object.
"""

import re
from collections.abc import Iterable

from shared.schemas.entities import ENTITY_CLASSES, SYNONYMS, EntityClass, entity_class

# Words too vague to ground: the class's default words are used instead.
_VAGUE = frozenset(
    ["object", "objects", "item", "items", "individual", "case", "container", "human"]
)
_DEFAULT_WORDS: dict[EntityClass, tuple[str, ...]] = {
    "person": ("person",),
    "vehicle": ("car", "truck", "van", "bicycle", "motorcycle"),
    "portable_object": ("bag", "backpack", "suitcase", "box", "package"),
    "animal": ("animal",),
}
_IRREGULAR = {"people": "person", "persons": "person", "men": "man", "women": "woman",
              "children": "child", "buses": "bus", "boxes": "box"}  # fmt: skip
_WORD = re.compile(r"[a-z]+")
# "Someone leaves a bag": people the synonym table doesn't list as an entity type.
_SOMEONE = frozenset(["someone", "somebody", "anyone", "anybody", "everyone", "nobody"])


def _singular(word: str, known: frozenset[str]) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if word.endswith("es") and word[:-2] in known:
        return word[:-2]
    if word.endswith("s") and word[:-1] in known:
        return word[:-1]
    return word


def grounding_labels(text: str, classes: Iterable[str] = ()) -> list[str]:
    """Plain nouns to ground, in a stable order; empty means "every distinct object"."""
    wanted: list[EntityClass] = [c for c in ENTITY_CLASSES if c in set(classes)]
    words: list[str] = []
    named: set[EntityClass] = set()
    for raw in _WORD.findall(text.lower()):
        kind = "person" if raw in _SOMEONE else entity_class(raw)
        if kind is None:
            continue
        if kind not in wanted:
            wanted.append(kind)
        if kind == "person" or raw in _VAGUE:
            continue
        word = _singular(raw, SYNONYMS[kind])
        named.add(kind)
        if word not in words:
            words.append(word)
    labels: list[str] = []
    for kind in wanted:
        if kind == "person":
            labels.append("person")
        elif kind in named:
            labels += [w for w in words if entity_class(w) == kind]
        else:
            labels += _DEFAULT_WORDS[kind]
    return list(dict.fromkeys(labels))
