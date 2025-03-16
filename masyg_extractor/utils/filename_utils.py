import re
import uuid

def sanitize_generate_unique_filename(filename: str) -> str:
    """Sanitize filename and prepend a unique UUID."""
    sanitized = re.sub(r'[./#$\[\]]', '_', filename)
    unique_id = str(uuid.uuid4())
    return f"{unique_id}_{sanitized}"
