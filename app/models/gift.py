from sqlalchemy import CheckConstraint

from app.extensions import db

from .base import utcnow


class Gift(db.Model):
    __tablename__ = "gifts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    emoji = db.Column(db.String(16), nullable=False)
    description = db.Column(db.String(255), nullable=False, default="", server_default="")
    animation_url = db.Column(db.String(255))


class SentGift(db.Model):
    __tablename__ = "sent_gifts"

    id = db.Column(db.Integer, primary_key=True)
    gift_id = db.Column(
        db.Integer, db.ForeignKey("gifts.id", ondelete="RESTRICT"), nullable=False
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The timeline message that displays this gift inside the conversation.
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="SET NULL"), unique=True
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    gift = db.relationship("Gift")
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    message = db.relationship("Message")

    __table_args__ = (
        db.Index("ix_sent_gifts_recipient_created", "recipient_id", "created_at"),
        CheckConstraint("sender_id <> recipient_id", name="not_self"),
    )
