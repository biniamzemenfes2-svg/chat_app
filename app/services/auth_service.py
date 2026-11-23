"""Account creation, authentication and password changes."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User

# Verified against when the account does not exist, so "unknown user" and
# "wrong password" take about the same time and cannot be told apart.
_DUMMY_HASH = generate_password_hash("timing-equaliser-not-a-real-password")


class DuplicateAccount(Exception):
    """Raised when a username or email was taken between validation and insert."""

    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def username_taken(username: str) -> bool:
    return db.session.scalar(select(User.id).where(User.username == username)) is not None


def email_taken(email: str) -> bool:
    return db.session.scalar(select(User.id).where(User.email == email)) is not None


def get_user_by_username(username: str) -> User | None:
    return db.session.scalar(select(User).where(User.username == username.strip().lower()))


def find_user(identifier: str) -> User | None:
    """Look an account up by username or email (case-insensitive)."""
    identifier = identifier.strip().lower()
    column = User.email if "@" in identifier else User.username
    return db.session.scalar(select(User).where(column == identifier))


def register_user(*, username: str, email: str, display_name: str, password: str) -> User:
    user = User(username=username, email=email, display_name=display_name)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
        if "email" in constraint:
            raise DuplicateAccount("email") from exc
        if "username" in constraint:
            raise DuplicateAccount("username") from exc
        raise
    return user


def authenticate(identifier: str, password: str) -> User | None:
    """Return the user if the credentials are valid and the account is active."""
    user = find_user(identifier)
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if not user.check_password(password) or not user.is_active:
        return None
    return user


def change_password(user: User, new_password: str) -> None:
    """Set a new password and invalidate every other session of this user."""
    user.set_password(new_password)
    user.rotate_session_token()
    db.session.commit()
