from urllib.parse import urlsplit, urlunsplit


def default_accounting_return_to(
    integration: str,
) -> str:
    return f"/data/shore/{integration}"


def sanitize_accounting_return_to(
    integration: str,
    value: str | None,
) -> str:
    fallback = default_accounting_return_to(
        integration,
    )

    if not value or not isinstance(value, str):
        return fallback

    if "\\" in value:
        return fallback

    if any(ord(character) < 32 for character in value):
        return fallback

    try:
        parsed = urlsplit(value)
    except ValueError:
        return fallback

    # Return targets must always stay inside the MASYG frontend.
    if parsed.scheme or parsed.netloc:
        return fallback

    allowed_paths = {
        "/integration",
        f"/integration/{integration}",
        fallback,
    }

    if parsed.path not in allowed_paths:
        return fallback

    return urlunsplit(
        (
            "",
            "",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
