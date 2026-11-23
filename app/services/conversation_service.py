"""Private conversations: creation, membership and authorization lookups.

Every query that returns a conversation or its messages goes through
``get_member_conversation`` so that "is this user a participant?" is decided in
exactly one place, on the server, never from anything the client claims.
"""
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import Conversation, ConversationMember, Message, MessageRead, User
from app.models.base import utcnow


class ConversationError(ValueError):
    """A conversation cannot be created (message is user-facing)."""


def get_member_conversation(conversation_id: int, user_id: int) -> Conversation | None:
    """Return the conversation only if ``user_id`` is a participant, else None.

    Callers answer "not found" for both a missing conversation and one the user
    does not belong to, so conversation ids reveal nothing.
    """
    return db.session.scalar(
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(Conversation.id == conversation_id, ConversationMember.user_id == user_id)
    )


def member_ids(conversation_id: int) -> list[int]:
    return list(
        db.session.scalars(
            select(ConversationMember.user_id).where(
                ConversationMember.conversation_id == conversation_id
            )
        )
    )


def other_member_ids(conversation_id: int, user_id: int) -> list[int]:
    return [uid for uid in member_ids(conversation_id) if uid != user_id]


def peer_of(conversation_id: int, user_id: int) -> User | None:
    """The other participant of a private conversation."""
    return db.session.scalar(
        select(User)
        .join(ConversationMember, ConversationMember.user_id == User.id)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id != user_id,
        )
    )


def peer_ids(user_id: int) -> set[int]:
    """Everyone who shares at least one conversation with ``user_id``."""
    mine = select(ConversationMember.conversation_id).where(
        ConversationMember.user_id == user_id
    )
    return set(
        db.session.scalars(
            select(ConversationMember.user_id)
            .where(
                ConversationMember.conversation_id.in_(mine),
                ConversationMember.user_id != user_id,
            )
            .distinct()
        )
    )


def get_or_create_private(user: User, other: User) -> tuple[Conversation, bool]:
    """Find or create the single private conversation between two users."""
    if user.id == other.id:
        raise ConversationError("You can't start a conversation with yourself.")
    if not other.is_active:
        raise ConversationError("That user is not available.")

    key = Conversation.make_direct_key(user.id, other.id)
    existing = db.session.scalar(select(Conversation).where(Conversation.direct_key == key))
    if existing is not None:
        return existing, False

    conversation = Conversation(conversation_type="private", direct_key=key)
    conversation.members = [
        ConversationMember(user_id=user.id),
        ConversationMember(user_id=other.id),
    ]
    db.session.add(conversation)
    try:
        db.session.commit()
    except IntegrityError:
        # Both users clicked "Message" at the same moment; the unique
        # direct_key let exactly one insert win. Use that one.
        db.session.rollback()
        existing = db.session.scalar(
            select(Conversation).where(Conversation.direct_key == key)
        )
        if existing is None:
            raise
        return existing, False
    return conversation, True


def touch(conversation: Conversation) -> None:
    conversation.updated_at = utcnow()


def list_for_user(user_id: int) -> list[dict]:
    """Conversation summaries for the sidebar, newest activity first.

    Three queries total regardless of how many conversations there are:
    conversations with their peer, last messages, and unread counts.
    """
    Peer = aliased(ConversationMember)
    rows = db.session.execute(
        select(Conversation, User)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .join(
            Peer,
            and_(Peer.conversation_id == Conversation.id, Peer.user_id != user_id),
        )
        .join(User, User.id == Peer.user_id)
        .where(ConversationMember.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    ).all()
    if not rows:
        return []
    ids = [conversation.id for conversation, _ in rows]

    last_messages = {
        message.conversation_id: message
        for message in db.session.scalars(
            select(Message)
            .where(Message.conversation_id.in_(ids))
            .distinct(Message.conversation_id)
            .order_by(Message.conversation_id, Message.id.desc())
        )
    }

    already_read = (
        select(MessageRead.id)
        .where(MessageRead.message_id == Message.id, MessageRead.user_id == user_id)
        .exists()
    )
    unread = dict(
        db.session.execute(
            select(Message.conversation_id, func.count())
            .where(
                Message.conversation_id.in_(ids),
                Message.sender_id != user_id,
                Message.deleted_at.is_(None),
                ~already_read,
            )
            .group_by(Message.conversation_id)
        ).all()
    )

    return [
        {
            "conversation": conversation,
            "peer": peer,
            "last_message": last_messages.get(conversation.id),
            "unread": unread.get(conversation.id, 0),
        }
        for conversation, peer in rows
    ]
