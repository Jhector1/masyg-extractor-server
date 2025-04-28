import re


def extract_uuid(file_string: str) -> str:
    """
    Extracts the UUID from a given file string.

    The function assumes the string starts with a UUID followed by an underscore.
    For example:
      "25d16d63-c73b-45a6-8c83-57803fe286e2_invoice_S012189154_002_pdf-17:"
    will return "25d16d63-c73b-45a6-8c83-57803fe286e2".

    Args:
        file_string (str): The string containing the UUID.

    Returns:
        str: The extracted UUID.

    Raises:
        ValueError: If the UUID cannot be found in the string.
    """
    match = re.match(r"^(?P<uuid>[0-9a-fA-F-]+)_", file_string)
    if match:
        return match.group("uuid")
    raise ValueError("UUID not found in input string")

def parse_int(value: str) -> int:
    # Strip out anything that isn't a digit or minus sign
    cleaned = re.sub(r"[^\d\-]", "", value or "")
    return int(cleaned)

def parse_float(value: str) -> float:
    """
    Strip out anything except digits, decimal point, and minus sign,
    then convert to float.
    """
    if not value:
        raise ValueError("No value provided")
    # Remove all characters except digits, dot, and minus
    cleaned = re.sub(r"[^\d\.\-]", "", value)
    return float(cleaned)