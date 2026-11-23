from sqlalchemy import CheckConstraint

from app.extensions import db

from .base import utcnow


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT: deleting a user must not silently erase the other person's history.
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    body = db.Column(db.Text)
    message_type = db.Column(
        db.String(16), nullable=False, default="text", server_default="text"
    )
    # Chosen by the sending client. Together with sender_id it makes a retried
    # send idempotent: a lost acknowledgement cannot create a duplicate message.
    client_id = db.Column(db.String(36))
    reply_to_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="SET NULL")
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Delivery to the recipient's client (read receipts live in MessageRead).
    # A single column is correct for 1:1 chats; groups would need a table.
    delivered_at = db.Column(db.DateTime(timezone=True))
    edited_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))  # soft delete

    conversation = db.relationship("Conversation")
    sender = db.relationship("User")
    reply_to = db.relationship("Message", remote_side=[id])
    reads = db.relationship(
        "MessageRead", back_populates="message", cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reactions = db.relationship(
        "MessageReaction", back_populates="message", cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attachments = db.relationship(
        "Attachment", back_populates="message", cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.Index("ix_messages_conversation_created", "conversation_id", "created_at", "id"),
        db.UniqueConstraint("sender_id", "client_id", name="uq_message_sender_client"),
        CheckConstraint(
            "message_type IN ('text', 'attachment', 'gift', 'system')",
            name="type_valid",
        ),
        CheckConstraint(
            "message_type <> 'text' OR body IS NOT NULL", name="text_has_body"
        ),
    )


class MessageRead(db.Model):
    __tablename__ = "message_reads"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    read_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    message = db.relationship("Message", back_populates="reads")

    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="uq_message_reader"),
    )


class MessageReaction(db.Model):
    __tablename__ = "message_reactions"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    emoji = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    message = db.relationship("Message", back_populates="reactions")

    __table_args__ = (
        db.UniqueConstraint(
            "message_id", "user_id", "emoji", name="uq_reaction_per_user_emoji"
        ),
    )
