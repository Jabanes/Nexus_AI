"""Phone normalization. Dumb-but-useful E.164 for US/Canada (NANP).

This is intentionally minimal. When we move to a real DB we'll replace this
with phonenumbers/libphonenumber.
"""

import re


def normalize_phone(raw: str | None, default_country: str = "US") -> str | None:
    """
    Best-effort E.164 conversion.

    Returns +1XXXXXXXXXX for 10-digit US numbers,
    keeps +<digits> if already E.164-looking,
    None if not recognizable.
    """
    if not raw:
        return None

    s = str(raw).strip()
    if not s:
        return None

    # Already E.164
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        if 8 <= len(digits) <= 15:
            return f"+{digits}"
        return None

    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    if default_country == "US":
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"

    # Unknown shape — return raw digits prefixed with + as a guess
    if 8 <= len(digits) <= 15:
        return f"+{digits}"

    return None
