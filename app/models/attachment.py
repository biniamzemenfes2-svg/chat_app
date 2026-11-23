from sqlalchemy import CheckConstraint

from app.extensions import db


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    file_name = db.Column(db.String(255), nullable=False)  # original name, display only
    file_path = db.Column(db.String(512), nullable=False)  # generated name under UPLOAD_FOLDER
    file_type = db.Column(db.String(127), nullable=False)  # validated MIME type
    file_size = db.Column(db.BigInteger, nullable=False)

    message = db.relationship("Message", back_populates="attachments")

    __table_args__ = (
        CheckConstraint("file_size >= 0", name="size_non_negative"),
    )
