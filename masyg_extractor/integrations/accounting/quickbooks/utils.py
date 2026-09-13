import re
from typing import Any, Optional
from masyg_extractor.integrations.accounting.shared.identifiers import extract_uuid, safe_uuid_key

# Strict canonical UUID pattern (8-4-4-4-12 hex)


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
