"""Money handling.

Amounts are *always* integers expressed in minor units (euro cents), so no
floating point arithmetic is ever involved. At the API boundary they are
exchanged as decimal strings such as ``"12.50"``.
"""

import re

from .errors import ValidationError

# Largest absolute value accepted for a single posting: 99_999_999_999.99.
MAX_AMOUNT = 10**13 - 1

_AMOUNT_RE = re.compile(r"(-?)([0-9]{1,11})(?:\.([0-9]{1,2}))?")


def parse_amount(text: str) -> int:
    """Convert a decimal string (max 2 decimals) into minor units.

    >>> parse_amount("12.5")
    1250
    """
    if not isinstance(text, str):
        raise ValidationError("amount must be a string such as '12.50'")
    match = _AMOUNT_RE.fullmatch(text)
    if match is None:
        raise ValidationError(f"invalid amount {text!r}: expected up to 11 digits and 2 decimals")
    sign, whole, fraction = match.groups()
    minor_units = int(whole) * 100 + int((fraction or "").ljust(2, "0"))
    return -minor_units if sign else minor_units


def format_amount(minor_units: int) -> str:
    """Convert minor units into a decimal string with exactly 2 decimals.

    >>> format_amount(-5)
    '-0.05'
    """
    sign = "-" if minor_units < 0 else ""
    whole, fraction = divmod(abs(minor_units), 100)
    return f"{sign}{whole}.{fraction:02d}"
