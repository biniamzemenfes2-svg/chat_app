import hmac
import secrets

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, text, true
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
    # Embedded in the login session. Changing it (on password change) signs the
    # user out everywhere, including "remember me" cookies.
    session_token = db.Column(
        db.String(64),
        nullable=False,
        default=lambda: secrets.token_hex(16),
        server_default=text("replace(gen_random_uuid()::text, '-', '')"),
    )

    __table_args__ = (
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self) -> str:
        return f"{self.id}:{self.session_token}"

    def rotate_session_token(self) -> None:
        self.session_token = secrets.token_hex(16)

    @property
    def initials(self) -> str:
        return (self.display_name or self.username)[:1].upper()

    def __repr__(self) -> str:
        return f"<User {self.username}>"


@login_manager.user_loader
def load_user(session_id: str):
    """Resolve a session value ("<id>:<token>") to a user, or None."""
    try:
        raw_id, token = session_id.split(":", 1)
        user = db.session.get(User, int(raw_id))
    except (AttributeError, TypeError, ValueError):
        return None
    if user is None or not user.is_active:
        return None
    if not hmac.compare_digest(user.session_token, token):
        return None
    return user
