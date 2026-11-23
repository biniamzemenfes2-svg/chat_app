from sqlalchemy import CheckConstraint

from app.extensions import db

from .base import utcnow


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    conversation_type = db.Column(
        db.String(16), nullable=False, default="private", server_default="private"
    )
    # "<lower user id>:<higher user id>" for private chats, NULL for groups.
    # The unique constraint makes "one private chat per pair" a database
    # guarantee, so two simultaneous "Message" clicks cannot create duplicates.
    direct_key = db.Column(db.String(40), unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Touched whenever a message is sent; used to order the conversation list.
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    members = db.relationship(
        "ConversationMember",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "conversation_type IN ('private', 'group')", name="type_valid"
        ),
        CheckConstraint(
            "(conversation_type = 'private') = (direct_key IS NOT NULL)",
            name="direct_key_matches_type",
        ),
    )

    @staticmethod
    def make_direct_key(user_a_id: int, user_b_id: int) -> str:
        low, high = sorted((int(user_a_id), int(user_b_id)))
        return f"{low}:{high}"


class ConversationMember(db.Model):
    __tablename__ = "conversation_members"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    joined_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    conversation = db.relationship("Conversation", back_populates="members")
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_user"),
    )
