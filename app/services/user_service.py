from sqlalchemy import case, or_, select

from app.extensions import db
from app.models import User

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 32


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_users(query: str, *, exclude_id: int, limit: int = 10) -> list[User]:
    """Find active users by username prefix or display-name substring.

    Wildcards in the query are escaped so ``%`` and ``_`` are matched literally.
    """
    query = (query or "").strip().lower().lstrip("@")[:MAX_QUERY_LENGTH]
    if len(query) < MIN_QUERY_LENGTH:
        return []
    escaped = _escape_like(query)
    prefix = f"{escaped}%"
    return list(
        db.session.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                User.id != exclude_id,
                or_(
                    User.username.like(prefix, escape="\\"),
                    User.display_name.ilike(f"%{escaped}%", escape="\\"),
                ),
            )
            .order_by(
                case(
                    (User.username == query, 0),
                    (User.username.like(prefix, escape="\\"), 1),
                    else_=2,
                ),
                User.username,
            )
            .limit(limit)
        )
    )
