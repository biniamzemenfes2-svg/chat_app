import functools

from flask_login import current_user
from flask_socketio import disconnect

from app.extensions import socketio


def user_room(user_id: int) -> str:
    return f"user:{user_id}"


def emit_to_user(user_id: int, event: str, payload: dict) -> None:
    socketio.emit(event, payload, to=user_room(user_id))


def as_int(value) -> int | None:
    """Strict integer from untrusted input (JSON numbers only, never bools/strings)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def authenticated_only(handler):
    """Re-check the session on *every* event, not just at connect time.

    A connection can outlive its login (logout elsewhere, password change,
    deactivated account). The user is reloaded from the database for each event,
    so a revoked session stops working immediately and its socket is closed.
    """

    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            disconnect()
            return {"ok": False, "error": "Please sign in again."}
        return handler(*args, **kwargs)

    return wrapper


def disconnect_user(user_id: int) -> None:
    """Close every live connection of a user (used on logout / password change).

    Sessions that are still valid reconnect on their own; revoked ones are
    refused at the next handshake, so they stop receiving private messages.
    """
    from app.services.presence import tracker

    for sid in tracker.sids_for(user_id):
        socketio.server.disconnect(sid, namespace="/")
