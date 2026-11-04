"""Ages typed on the command line: '90s', '30m', '2h', '1d'."""

import argparse
import re
from datetime import timedelta

_AGE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_age(text: str) -> timedelta:
    """'90s', '30m', '2h', '1d' -> a timedelta. An argparse error otherwise, so it can be a
    `type=` of an argument."""
    match = _AGE.match(text.strip().lower())
    if match is None:
        raise argparse.ArgumentTypeError(f"'{text}' is not an age like 30m, 2h or 1d")
    return timedelta(**{_UNITS[match.group(2)]: float(match.group(1))})
