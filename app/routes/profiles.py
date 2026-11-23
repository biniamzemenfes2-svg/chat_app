from flask import Blueprint, abort, current_app, render_template, send_from_directory
from flask_login import login_required

from app.services import auth_service, avatar_service

bp = Blueprint("profiles", __name__)


@bp.get("/u/<username>")
@login_required
def view(username):
    user = auth_service.get_user_by_username(username)
    if user is None or not user.is_active:
        abort(404)
    return render_template("profile.html", profile_user=user)


@bp.get("/avatars/<filename>")
@login_required
def avatar(filename):
    # Only names we generated ourselves; anything else (including path
    # traversal attempts) is a 404.
    if not avatar_service.FILENAME_RE.match(filename):
        abort(404)
    return send_from_directory(
        avatar_service.avatar_dir(),
        filename,
        mimetype="image/webp",
        max_age=60 * 60 * 24,
    )
