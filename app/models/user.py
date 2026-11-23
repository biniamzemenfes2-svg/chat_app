from flask_login import UserMixin
from sqlalchemy import CheckConstraint, true
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

from .base import utcnow


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # Stored lowercase (enforced by a CHECK) so uniqueness is case-insensitive.
    username = db.Column(db.String(32), nullable=False, unique=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(64), nullable=False)
    bio = db.Column(db.String(280), nullable=False, default="", server_default="")
    avatar = db.Column(db.String(255))  # stored file name, not a user path
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen = db.Column(db.DateTime(timezone=True))
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )

    __table_args__ = (
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.username}>"


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
