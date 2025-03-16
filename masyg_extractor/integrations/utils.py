from datetime import datetime
from typing import Dict, Any

def format_date(date_str: str) -> str:
    """
    Converts a date string to ISO format (YYYY-MM-DD) using several common formats.
    """
    formats = [
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
        "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
    ]
    for fmt_str in formats:
        try:
            dt = datetime.strptime(date_str, fmt_str)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Date format not recognized for '{date_str}'.")

def nocache_headers(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attaches headers to disable caching for sensitive responses.
    """
    response_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    if isinstance(response, dict):
        response["headers"] = response_headers
    return response
