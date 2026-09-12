from datetime import datetime


def generate_group_id() -> str:
    """Generate a unique group ID based on the current timestamp."""
    return datetime.now().strftime("%Y%m%d%H%M%S")
