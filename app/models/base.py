from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware current UTC time, used for column defaults."""
    return datetime.now(timezone.utc)
