import hashlib
import re


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


def safe_uuid_key(s: str) -> str:
    """
    Return first 20 chars of the UUID in s if present; otherwise a stable 20-char hash key.
    """
    try:
        return extract_uuid(s)[:20]
    except Exception:
        token = (s or "")
        return hashlib.sha1(token.encode("utf-8")).hexdigest()[:20]
