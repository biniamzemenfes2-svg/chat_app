from flask import current_app
from flask_login import current_user

from app.extensions import socketio
from app.services import conversation_service, message_service
from app.services.presence import tracker
from app.utils import socket_limiter

from .helpers import as_int, authenticated_only, emit_to_user

NOT_FOUND = "Conversation not found."


def _error(message: str) -> dict:
    return {"ok": False, "error": message}


@socketio.on("send_message")
@authenticated_only
def on_send_message(data):
    if not isinstance(data, dict):
        return _error("Invalid request.")

    config = current_app.config
    if not socket_limiter.allow(
        f"msg:{current_user.id}",
        config["SOCKET_MESSAGES_PER_WINDOW"],
        config["SOCKET_MESSAGE_WINDOW_SECONDS"],
    ):
        return _error("You're sending messages too quickly. Please slow down.")

    # Authorization: the conversation id is only a hint from the client. It is
    # honoured solely if this user is a participant.
    conversation_id = as_int(data.get("conversation_id"))
    conversation = (
        conversation_service.get_member_conversation(conversation_id, current_user.id)
        if conversation_id is not None
        else None
    )
    if conversation is None:
        return _error(NOT_FOUND)

    sender = current_user._get_current_object()
    recipients = conversation_service.other_member_ids(conversation.id, sender.id)
    try:
        message, created = message_service.send_message(
            conversation,
            sender,
            data.get("body"),
            client_id=data.get("client_id"),
            delivered=any(tracker.is_online(uid) for uid in recipients),
            reply_to_id=data.get("reply_to_id"),
        )
    except message_service.MessageError as exc:
        return _error(str(exc))

    payload = message_service.serialize(message)
    if created:
        # Also to the sender's own room so their other tabs/devices stay in sync.
        for user_id in [sender.id, *recipients]:
            emit_to_user(user_id, "message_new", payload)
    return {"ok": True, "message": payload}


@socketio.on("typing")
@authenticated_only
def on_typing(data):
    if not isinstance(data, dict):
        return
    if not socket_limiter.allow(f"typing:{current_user.id}", 30, 10):
        return  # silently drop; typing hints are best-effort
    conversation_id = as_int(data.get("conversation_id"))
    if conversation_id is None:
        return
    if conversation_service.get_member_conversation(conversation_id, current_user.id) is None:
        return
    payload = {
        "conversation_id": conversation_id,
        "user_id": current_user.id,
        "typing": bool(data.get("typing")),
    }
    for user_id in conversation_service.other_member_ids(conversation_id, current_user.id):
        emit_to_user(user_id, "typing", payload)


@socketio.on("mark_read")
@authenticated_only
def on_mark_read(data):
    if not isinstance(data, dict):
        return _error("Invalid request.")
    conversation_id = as_int(data.get("conversation_id"))
    conversation = (
        conversation_service.get_member_conversation(conversation_id, current_user.id)
        if conversation_id is not None
        else None
    )
    if conversation is None:
        return _error(NOT_FOUND)

    reader_id = current_user.id
    ids = message_service.mark_read(conversation.id, reader_id)
    if ids:
        for user_id in conversation_service.other_member_ids(conversation.id, reader_id):
            emit_to_user(
                user_id,
                "messages_read",
                {"conversation_id": conversation.id, "reader_id": reader_id, "message_ids": ids},
            )
        # The reader's other tabs clear their unread badge.
        emit_to_user(reader_id, "conversation_read", {"conversation_id": conversation.id})
    return {"ok": True, "count": len(ids)}


# ------------------------------------------------------ edit / delete / react

MESSAGE_NOT_FOUND = "Message not found."


def _member_message(data):
    """The message named in ``data``, only if the caller is in its conversation.

    Same answer for "doesn't exist" and "not yours to see", so ids reveal nothing.
    """
    if not isinstance(data, dict):
        return None
    message_id = as_int(data.get("message_id"))
    if message_id is None:
        return None
    return message_service.get_message_for_member(message_id, current_user.id)


def _publish_update(message) -> dict:
    """Tell every participant the message changed (edit, delete or reactions)."""
    payload = message_service.serialize(message)
    for user_id in conversation_service.member_ids(message.conversation_id):
        emit_to_user(user_id, "message_updated", payload)
    return payload


def _allowed(bucket: str, limit: int, window: float) -> bool:
    return socket_limiter.allow(f"{bucket}:{current_user.id}", limit, window)


@socketio.on("edit_message")
@authenticated_only
def on_edit_message(data):
    config = current_app.config
    if not _allowed("msg", config["SOCKET_MESSAGES_PER_WINDOW"], config["SOCKET_MESSAGE_WINDOW_SECONDS"]):
        return _error("You're doing that too quickly. Please slow down.")
    message = _member_message(data)
    if message is None:
        return _error(MESSAGE_NOT_FOUND)
    try:
        changed = message_service.edit_message(
            message, current_user._get_current_object(), data.get("body")
        )
    except message_service.MessageError as exc:
        return _error(str(exc))
    payload = _publish_update(message) if changed else message_service.serialize(message)
    return {"ok": True, "message": payload}


@socketio.on("delete_message")
@authenticated_only
def on_delete_message(data):
    config = current_app.config
    if not _allowed("msg", config["SOCKET_MESSAGES_PER_WINDOW"], config["SOCKET_MESSAGE_WINDOW_SECONDS"]):
        return _error("You're doing that too quickly. Please slow down.")
    message = _member_message(data)
    if message is None:
        return _error(MESSAGE_NOT_FOUND)
    try:
        changed = message_service.delete_message(message, current_user._get_current_object())
    except message_service.MessageError as exc:
        return _error(str(exc))
    payload = _publish_update(message) if changed else message_service.serialize(message)
    return {"ok": True, "message": payload}


@socketio.on("react")
@authenticated_only
def on_react(data):
    if not _allowed("react", 30, 10):
        return _error("You're reacting too quickly. Please slow down.")
    message = _member_message(data)
    if message is None:
        return _error(MESSAGE_NOT_FOUND)
    try:
        outcome = message_service.toggle_reaction(
            message, current_user._get_current_object(), data.get("emoji")
        )
    except message_service.MessageError as exc:
        return _error(str(exc))
    payload = _publish_update(message)
    return {"ok": True, "outcome": outcome, "message": payload}
