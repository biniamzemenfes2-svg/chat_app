from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.extensions import limiter
from app.services import user_service

from .serializers import user_summary

bp = Blueprint("users", __name__)


def _per_user() -> str:
    return f"user-search:{current_user.get_id()}"


@bp.get("/api/users/search")
@login_required
@limiter.limit(lambda: current_app.config["USER_SEARCH_LIMIT"], key_func=_per_user)
def search():
    users = user_service.search_users(
        request.args.get("q", ""), exclude_id=current_user.id
    )
    # Only what a stranger needs to recognise someone: never email or presence.
    return jsonify(users=[user_summary(user) for user in users])
