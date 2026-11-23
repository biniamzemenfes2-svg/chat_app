"""Sending, loading and tracking the state of messages."""
import re

from flask import current_app
from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Conversation, Message, MessageRead
from app.models.base import utcnow

from . import conversation_service

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,36}$")
# Control characters other than tab and newline are never legitimate in chat text.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HISTORY_PAGE_SIZE = 50
PREVIEW_LENGTH = 80


class MessageError(ValueError):
    """The message cannot be accepted (message is user-facing)."""


def clean_body(body) -> str:
    if not isinstance(body, str):
        raise MessageError("Message must be text.")
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise MessageError("Message is empty.")
    if len(body) > current_app.config["MESSAGE_MAX_LENGTH"]:
        raise MessageError(
            f"Message is too long (maximum {current_app.config['MESSAGE_MAX_LENGTH']} characters)."
        )
    if _CONTROL_CHARS.search(body):
        raise MessageError("Message contains invalid characters.")
    return body


def send_message(
    conversation: Conversation, sender, body, *, client_id=None, delivered: bool = False
) -> tuple[Message, bool]:
    """Persist a text message. Returns (message, created).

    ``created`` is False when ``client_id`` matches a message this sender already
    sent, which makes a retry after a lost acknowledgement harmless.
    """
    body = clean_body(body)
    if client_id is not None and not (
        isinstance(client_id, str) and CLIENT_ID_RE.match(client_id)
    ):
        raise MessageError("Invalid client id.")

    if client_id is not None:
        existing = _find_by_client_id(sender.id, client_id)
        if existing is not None:
            return existing, False

    now = utcnow()
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        body=body,
        message_type="text",
        client_id=client_id,
        delivered_at=now if delivered else None,
    )
    db.session.add(message)
    conversation_service.touch(conversation)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = _find_by_client_id(sender.id, client_id) if client_id else None
        if existing is None:
            raise
        return existing, False
    return message, True


def _find_by_client_id(sender_id: int, client_id: str) -> Message | None:
    return db.session.scalar(
        select(Message).where(Message.sender_id == sender_id, Message.client_id == client_id)
    )


def serialize(message: Message, read_at=None) -> dict:
    deleted = message.deleted_at is not None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "type": message.message_type,
        "body": None if deleted else message.body,
        "deleted": deleted,
        "client_id": message.client_id,
        "created_at": message.created_at.isoformat(),
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": read_at.isoformat() if read_at else None,
    }


def preview(message: Message | None) -> dict | None:
    if message is None:
        return None
    text = "This message was deleted" if message.deleted_at else (message.body or "")
    text = " ".join(text.split())
    if len(text) > PREVIEW_LENGTH:
        text = text[: PREVIEW_LENGTH - 1] + "…"
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "type": message.message_type,
        "text": text,
        "created_at": message.created_at.isoformat(),
    }


def history(conversation_id: int, peer_id: int, *, before_id: int | None = None,
            limit: int = HISTORY_PAGE_SIZE) -> tuple[list[dict], bool]:
    """A page of messages, oldest first, plus whether older ones exist.

    ``peer_id`` is the other participant, whose read receipts decide the
    "read" state of messages we sent.
    """
    limit = max(1, min(int(limit), 100))
    query = (
        select(Message, MessageRead.read_at)
        .outerjoin(
            MessageRead,
            and_(MessageRead.message_id == Message.id, MessageRead.user_id == peer_id),
        )
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        query = query.where(Message.id < before_id)
    rows = db.session.execute(query).all()
    has_more = len(rows) > limit
    page = list(reversed(rows[:limit]))
    return [serialize(message, read_at) for message, read_at in page], has_more


def mark_read(conversation_id: int, user_id: int) -> list[int]:
    """Mark everything others sent in this conversation as read by ``user_id``.

    Returns the ids newly marked (empty if there was nothing unread). Anything
    read is necessarily delivered, so that is filled in too.
    """
    already_read = (
        select(MessageRead.id)
        .where(MessageRead.message_id == Message.id, MessageRead.user_id == user_id)
        .exists()
    )
    ids = list(
        db.session.scalars(
            select(Message.id).where(
                Message.conversation_id == conversation_id,
                Message.sender_id != user_id,
                Message.deleted_at.is_(None),
                ~already_read,
            )
        )
    )
    if not ids:
        return []
    now = utcnow()
    db.session.execute(
        pg_insert(MessageRead)
        .values([{"message_id": mid, "user_id": user_id, "read_at": now} for mid in ids])
        .on_conflict_do_nothing(constraint="uq_message_reader")
    )
    db.session.execute(
        update(Message)
        .where(Message.id.in_(ids), Message.delivered_at.is_(None))
        .values(delivered_at=now)
    )
    db.session.commit()
    return ids


def mark_delivered_for_user(user_id: int) -> dict[tuple[int, int], list[int]]:
    """Messages waiting for ``user_id`` count as delivered once they connect.

    Returns {(conversation_id, sender_id): [message ids]} so the senders can be told.
    """
    from app.models import ConversationMember

    mine = select(ConversationMember.conversation_id).where(
        ConversationMember.user_id == user_id
    )
    rows = db.session.execute(
        update(Message)
        .where(
            Message.conversation_id.in_(mine),
            Message.sender_id != user_id,
            Message.delivered_at.is_(None),
        )
        .values(delivered_at=utcnow())
        .returning(Message.id, Message.conversation_id, Message.sender_id)
    ).all()
    db.session.commit()
    grouped: dict[tuple[int, int], list[int]] = {}
    for message_id, conversation_id, sender_id in rows:
        grouped.setdefault((conversation_id, sender_id), []).append(message_id)
    return grouped
