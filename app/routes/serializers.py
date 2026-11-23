"""JSON shapes shared by the API routes."""
from flask import url_for

from app.services import message_service
from app.services.presence import tracker


def avatar_url(user) -> str | None:
    return url_for("profiles.avatar", filename=user.avatar) if user.avatar else None


def user_summary(user, *, with_presence: bool = False) -> dict:
    data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "avatar_url": avatar_url(user),
        "profile_url": url_for("profiles.view", username=user.username),
    }
    if with_presence:
        data["online"] = tracker.is_online(user.id)
        data["last_seen"] = user.last_seen.isoformat() if user.last_seen else None
    return data


def conversation_summary(item: dict) -> dict:
    conversation = item["conversation"]
    return {
        "id": conversation.id,
        "url": url_for("conversations.view", conversation_id=conversation.id),
        "peer": user_summary(item["peer"], with_presence=True),
        "last_message": message_service.preview(item["last_message"]),
        "unread": item["unread"],
        "updated_at": conversation.updated_at.isoformat(),
    }
