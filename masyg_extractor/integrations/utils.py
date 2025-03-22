from datetime import datetime
from typing import Dict, Any

from datetime import datetime

def format_date(date_str: str) -> str:
    """
    Converts a date string to ISO format (YYYY-MM-DD) using several common formats.
    Tries a wide range of date formats to accommodate various input styles.
    """
    formats = [
        "%Y-%m-%d",     # 2020-12-31
        "%m/%d/%Y",     # 12/31/2020
        "%m/%d/%y",     # 12/31/20
        "%B %d, %Y",    # December 31, 2020
        "%b %d, %Y",    # Dec 31, 2020
        "%d %B %Y",     # 31 December 2020
        "%d %b %Y",     # 31 Dec 2020
        "%d/%m/%Y",     # 31/12/2020
        "%d-%m-%Y",     # 31-12-2020
        "%Y/%m/%d",     # 2020/12/31
        "%Y.%m.%d",     # 2020.12.31
        "%d.%m.%Y",     # 31.12.2020
        "%d.%m.%y",     # 31.12.20
        "%B %d %Y",     # December 31 2020
        "%b %d %Y",     # Dec 31 2020
        "%d %B, %Y",    # 31 December, 2020
        "%d %b, %Y",    # 31 Dec, 2020
        "%Y%m%d",       # 20201231
        "%d%m%Y",       # 31122020
        "%m%d%Y",       # 12312020
        "%d-%b-%Y",     # 31-Dec-2020
        "%d-%b-%y",     # 31-Dec-20
        "%d-%B-%Y",     # 31-December-2020
        "%d-%B-%y",     # 31-December-20
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
