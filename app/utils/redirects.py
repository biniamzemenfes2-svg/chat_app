from urllib.parse import urlsplit


def safe_next_url(target: str | None) -> str | None:
    """Return ``target`` only if it is a plain same-site path, else None.

    Prevents open redirects through ``?next=``: absolute URLs, scheme-relative
    URLs (``//evil.com``), backslash tricks and control characters are refused.
    """
    if not target:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return None
    if "\\" in target or not target.startswith("/") or target.startswith("//"):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target
