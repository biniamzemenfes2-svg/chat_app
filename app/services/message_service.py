"""Sending, loading and tracking the state of messages."""
import re
from typing import NamedTuple

from flask import current_app
from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageReaction,
    MessageRead,
)
from app.models.base import utcnow

from . import conversation_service, emoji

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,36}$")
# Control characters other than tab and newline are never legitimate in chat text.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HISTORY_PAGE_SIZE = 50
PREVIEW_LENGTH = 80
QUOTE_LENGTH = 120
SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 100
SEARCH_LIMIT = 30


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
    conversation: Conversation, sender, body, *, client_id=None, delivered: bool = False,
    reply_to_id=None,
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

    if reply_to_id is not None:
        target = (
            db.session.get(Message, reply_to_id)
            if isinstance(reply_to_id, int) and not isinstance(reply_to_id, bool)
            else None
        )
        # Replies may only point at a live message of this same conversation.
        if target is None or target.conversation_id != conversation.id or target.deleted_at:
            raise MessageError("The message you're replying to is no longer available.")

    now = utcnow()
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        body=body,
        message_type="text",
        client_id=client_id,
        reply_to_id=reply_to_id,
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
    return serialize_many([(message, read_at)])[0]


def serialize_many(rows: list[tuple[Message, object]]) -> list[dict]:
    """Serialize messages with their reactions and reply quotes in two extra queries."""
    messages = [message for message, _ in rows]
    reactions = _load_reactions([m.id for m in messages])
    targets = _load_reply_targets(messages)

    result = []
    for message, read_at in rows:
        deleted = message.deleted_at is not None
        quote = None
        target = targets.get(message.reply_to_id)
        if target is not None and target.conversation_id == message.conversation_id:
            quote = {
                "id": target.id,
                "sender_id": target.sender_id,
                "text": "" if target.deleted_at else _one_line(target.body, QUOTE_LENGTH),
                "deleted": target.deleted_at is not None,
            }
        result.append(
            {
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
                "edited_at": message.edited_at.isoformat() if message.edited_at else None,
                "reply_to": quote,
                "reactions": [] if deleted else reactions.get(message.id, []),
            }
        )
    return result


def _load_reactions(message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    grouped: dict[int, dict[str, list[int]]] = {}
    for reaction in db.session.scalars(
        select(MessageReaction)
        .where(MessageReaction.message_id.in_(message_ids))
        .order_by(MessageReaction.id)
    ):
        grouped.setdefault(reaction.message_id, {}).setdefault(reaction.emoji, []).append(
            reaction.user_id
        )
    return {
        message_id: [{"emoji": e, "user_ids": ids} for e, ids in by_emoji.items()]
        for message_id, by_emoji in grouped.items()
    }


def _load_reply_targets(messages: list[Message]) -> dict[int, Message]:
    ids = {m.reply_to_id for m in messages if m.reply_to_id}
    if not ids:
        return {}
    return {t.id: t for t in db.session.scalars(select(Message).where(Message.id.in_(ids)))}


def _one_line(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def preview(message: Message | None) -> dict | None:
    if message is None:
        return None
    text = "This message was deleted" if message.deleted_at else (message.body or "")
    text = _one_line(text, PREVIEW_LENGTH)
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "type": message.message_type,
        "text": text,
        "created_at": message.created_at.isoformat(),
    }


class HistoryPage(NamedTuple):
    messages: list[dict]
    has_older: bool
    has_newer: bool


def history_page(
    conversation_id: int,
    peer_id: int,
    *,
    before_id: int | None = None,
    after_id: int | None = None,
    around_id: int | None = None,
    limit: int = HISTORY_PAGE_SIZE,
) -> HistoryPage | None:
    """A window of messages, oldest first.

    Default is the newest page; ``before_id`` / ``after_id`` page backwards /
    forwards; ``around_id`` centres on one message (for jumping to a search hit
    or a quoted reply). Returns None if ``around_id`` is not in this conversation.

    ``peer_id`` is the other participant, whose read receipts decide the "read"
    state of messages we sent.
    """
    limit = max(1, min(int(limit), 100))
    base = (
        select(Message, MessageRead.read_at)
        .outerjoin(
            MessageRead,
            and_(MessageRead.message_id == Message.id, MessageRead.user_id == peer_id),
        )
        .where(Message.conversation_id == conversation_id)
    )

    if around_id is not None:
        exists = db.session.scalar(
            select(Message.id).where(
                Message.id == around_id, Message.conversation_id == conversation_id
            )
        )
        if exists is None:
            return None
        before_count = limit // 2
        after_count = limit - before_count
        older = db.session.execute(
            base.where(Message.id < around_id).order_by(Message.id.desc()).limit(before_count + 1)
        ).all()
        newer = db.session.execute(
            base.where(Message.id >= around_id).order_by(Message.id).limit(after_count + 1)
        ).all()
        rows = list(reversed(older[:before_count])) + newer[:after_count]
        return HistoryPage(
            serialize_many(rows), len(older) > before_count, len(newer) > after_count
        )

    if after_id is not None:
        rows = db.session.execute(
            base.where(Message.id > after_id).order_by(Message.id).limit(limit + 1)
        ).all()
        return HistoryPage(serialize_many(rows[:limit]), True, len(rows) > limit)

    query = base.order_by(Message.id.desc()).limit(limit + 1)
    if before_id is not None:
        query = base.where(Message.id < before_id).order_by(Message.id.desc()).limit(limit + 1)
    rows = db.session.execute(query).all()
    return HistoryPage(
        serialize_many(list(reversed(rows[:limit]))), len(rows) > limit, False
    )


def history(conversation_id: int, peer_id: int, *, before_id: int | None = None,
            limit: int = HISTORY_PAGE_SIZE) -> tuple[list[dict], bool]:
    """The newest page (or the page before ``before_id``) and whether older ones exist."""
    page = history_page(conversation_id, peer_id, before_id=before_id, limit=limit)
    return page.messages, page.has_older


def search_messages(conversation_id: int, query: str, *, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Case-insensitive substring search of one conversation's live text messages."""
    query = (query or "").strip()[:SEARCH_MAX_LENGTH]
    if len(query) < SEARCH_MIN_LENGTH:
        return []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    messages = db.session.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.deleted_at.is_(None),
            Message.message_type == "text",
            Message.body.ilike(f"%{escaped}%", escape="\\"),
        )
        .order_by(Message.id.desc())
        .limit(limit)
    ).all()
    return serialize_many([(message, None) for message in messages])


# ------------------------------------------------------- edit / delete / react

def get_message_for_member(message_id: int, user_id: int) -> Message | None:
    """The message, only if ``user_id`` participates in its conversation."""
    return db.session.scalar(
        select(Message)
        .join(ConversationMember, ConversationMember.conversation_id == Message.conversation_id)
        .where(Message.id == message_id, ConversationMember.user_id == user_id)
    )


def edit_message(message: Message, editor, body) -> bool:
    """Replace the text of the editor's own message. Returns False if unchanged."""
    if message.sender_id != editor.id:
        raise MessageError("You can only edit your own messages.")
    if message.deleted_at is not None:
        raise MessageError("This message was deleted.")
    if message.message_type != "text":
        raise MessageError("Only text messages can be edited.")
    body = clean_body(body)
    if body == message.body:
        return False
    message.body = body
    message.edited_at = utcnow()
    db.session.commit()
    return True


def delete_message(message: Message, user) -> bool:
    """Delete for everyone. The text itself is erased, not just hidden."""
    if message.sender_id != user.id:
        raise MessageError("You can only delete your own messages.")
    if message.deleted_at is not None:
        return False
    message.deleted_at = utcnow()
    message.body = ""
    db.session.execute(delete(MessageReaction).where(MessageReaction.message_id == message.id))
    db.session.commit()
    return True


def toggle_reaction(message: Message, user, emoji_char) -> str:
    """React with ``emoji_char``; reacting again with it removes the reaction.

    Each user has at most one reaction per message: picking a different emoji
    replaces the previous one. Returns "added", "changed" or "removed".
    """
    if not emoji.is_valid_emoji(emoji_char):
        raise MessageError("That emoji can't be used as a reaction.")
    if message.deleted_at is not None:
        raise MessageError("This message was deleted.")

    existing = list(
        db.session.scalars(
            select(MessageReaction).where(
                MessageReaction.message_id == message.id,
                MessageReaction.user_id == user.id,
            )
        )
    )
    if any(r.emoji == emoji_char for r in existing):
        outcome = "removed"
        new = None
    else:
        outcome = "changed" if existing else "added"
        new = MessageReaction(message_id=message.id, user_id=user.id, emoji=emoji_char)
    for reaction in existing:
        db.session.delete(reaction)
    if new is not None:
        db.session.add(new)
    try:
        db.session.commit()
    except IntegrityError:  # double-click race on the same (message, user, emoji)
        db.session.rollback()
    return outcome


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
