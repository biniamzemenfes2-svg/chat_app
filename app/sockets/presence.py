from flask import request
from flask_login import current_user
from flask_socketio import emit, join_room
from sqlalchemy import update

from app.extensions import db, socketio
from app.models import User
from app.models.base import utcnow
from app.services import conversation_service, message_service
from app.services.presence import tracker

from .helpers import emit_to_user, user_room


@socketio.on("connect")
def handle_connect(auth):
    """Only authenticated users may hold a Socket.IO connection.

    Each user joins a room named after their own id; everything addressed to
    them (messages, typing, presence, call signaling) is delivered there. The id
    comes from the server-side session, never from anything the client sends,
    and clients cannot join any other room.
    """
    if not current_user.is_authenticated:
        return False

    user_id = current_user.id
    join_room(user_room(user_id))
    came_online = tracker.connect(user_id, request.sid)
    try:
        # Messages that arrived while this user was away are now delivered.
        for (conversation_id, sender_id), ids in message_service.mark_delivered_for_user(
            user_id
        ).items():
            emit_to_user(
                sender_id,
                "messages_delivered",
                {"conversation_id": conversation_id, "message_ids": ids},
            )

        peers = conversation_service.peer_ids(user_id)
        emit("presence_state", {"online": tracker.online_among(peers)})
        if came_online:
            for peer_id in peers:
                emit_to_user(peer_id, "presence", {"user_id": user_id, "online": True})
    except Exception:
        tracker.disconnect(request.sid)
        raise


@socketio.on("disconnect")
def handle_disconnect(*args):
    # Identify the user from our own sid map: by now the session may be revoked.
    user_id, went_offline = tracker.disconnect(request.sid)
    if user_id is None or not went_offline:
        return

    now = utcnow()
    db.session.execute(update(User).where(User.id == user_id).values(last_seen=now))
    db.session.commit()
    for peer_id in conversation_service.peer_ids(user_id):
        emit_to_user(
            peer_id,
            "presence",
            {"user_id": user_id, "online": False, "last_seen": now.isoformat()},
        )
