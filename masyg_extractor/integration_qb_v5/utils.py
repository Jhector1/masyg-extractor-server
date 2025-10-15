import re
from typing import Any, Optional

# Strict canonical UUID pattern (8-4-4-4-12 hex)
_UUID_RE = re.compile(
    r"(?i)\b(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)

def extract_uuid(file_string: str, require_leading: bool = False) -> str:
    """
    Extract the first RFC-4122 style UUID from `file_string`.

    If `require_leading=True`, the UUID must appear at the very start,
    optionally followed by an underscore (e.g. '<uuid>_rest_of_name').
    """
    if not isinstance(file_string, str):
        raise ValueError("file_string must be a string")

    if require_leading:
        # Match only at start; allow optional trailing underscore
        m = re.match(rf"^{_UUID_RE.pattern}(?=(_|$))", file_string)
    else:
        m = _UUID_RE.search(file_string)

    if not m:
        raise ValueError("UUID not found in input string")

    return m.group("uuid")


def parse_int(value: Any, default: int = 0) -> int:
    """
    Robust int parser.
    Accepts int/float/str/None and ignores thousands separators and currency symbols.
    Keeps a single leading sign.
    """
    if value is None:
        return int(default)
    if isinstance(value, bool):  # avoid True -> 1
        return int(default)
    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip()
    if s == "":
        return int(default)

    # Remove commas/spaces/currency etc., keep digits and a single leading sign.
    # Example: "$ 1,234.00" -> "1234.00" -> int(1234)
    s = s.replace(",", "")
    m = re.match(r"^[\+\-]?\d+", s)
    if not m:
        return int(default)
    return int(m.group(0))


def parse_float(value: Any, default: float = 0.0) -> float:
    """
    Robust float parser.
    Accepts int/float/str/None and ignores thousands separators and currency symbols.
    Keeps one decimal point and a single leading sign.
    """
    if value is None:
        return float(default)
    if isinstance(value, bool):
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if s == "":
        return float(default)

    # Normalize: drop commas, then extract sign + digits + optional .fraction
    s = s.replace(",", "")
    m = re.match(r"^[\+\-]?\d*(?:\.\d+)?", s)
    if not m:
        return float(default)
    token = m.group(0)
    if token in ("", "+", "-"):
        return float(default)
    return float(token)
# utils.py (or wherever extract_uuid is defined)

import hashlib

def safe_uuid_key(s: str) -> str:
    """
    Return first 20 chars of the UUID in s if present; otherwise a stable 20-char hash key.
    """
    try:
        return extract_uuid(s)[:20]
    except Exception:
        token = (s or "")
        return hashlib.sha1(token.encode("utf-8")).hexdigest()[:20]
