from sqlalchemy import CheckConstraint

from app.extensions import db

from .base import utcnow


class Call(db.Model):
    """One call attempt between two users (a row per ring, not per media track)."""

    __tablename__ = "calls"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    caller_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    receiver_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    call_type = db.Column(db.String(8), nullable=False)
    status = db.Column(
        db.String(16), nullable=False, default="ringing", server_default="ringing"
    )
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)  # rang
    answered_at = db.Column(db.DateTime(timezone=True))
    ended_at = db.Column(db.DateTime(timezone=True))

    caller = db.relationship("User", foreign_keys=[caller_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])

    __table_args__ = (
        CheckConstraint("call_type IN ('audio', 'video')", name="type_valid"),
        CheckConstraint(
            "status IN ('ringing', 'accepted', 'rejected', 'cancelled', "
            "'missed', 'ended', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("caller_id <> receiver_id", name="not_self"),
    )
