from flask import Blueprint, flash, redirect, render_template, request, current_app, url_for
from flask_login import current_user, login_required, login_user
from flask_login.config import COOKIE_NAME as DEFAULT_REMEMBER_COOKIE

from app.extensions import db
from app.forms import ChangePasswordForm, ProfileForm
from app.services import auth_service, avatar_service
from app.sockets.helpers import disconnect_user

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.get("/")
@login_required
def index():
    return redirect(url_for("settings.profile"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.display_name = form.display_name.data
        current_user.bio = form.bio.data or ""

        stale_file = None
        if form.processed_avatar is not None:
            stale_file = avatar_service.save_avatar(current_user, form.processed_avatar)
        elif form.remove_avatar.data:
            stale_file = avatar_service.clear_avatar(current_user)

        db.session.commit()
        avatar_service.discard_file(stale_file)
        flash("Profile updated.", "success")
        return redirect(url_for("settings.profile"))

    status = 400 if request.method == "POST" else 200
    return render_template("settings/profile.html", form=form), status


@bp.route("/password", methods=["GET", "POST"])
@login_required
def password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        cookie_name = current_app.config.get("REMEMBER_COOKIE_NAME", DEFAULT_REMEMBER_COOKIE)
        keep_remembered = cookie_name in request.cookies
        user = current_user._get_current_object()  # the real User, not the proxy
        auth_service.change_password(user, form.new_password.data)
        # The token changed, so this session is now stale too: sign this
        # browser straight back in; every other session stays signed out.
        login_user(user, remember=keep_remembered)
        # Live connections authenticated with the old token must not keep
        # receiving private messages; valid sessions simply reconnect.
        disconnect_user(user.id)
        flash("Password changed. Other devices have been signed out.", "success")
        return redirect(url_for("settings.password"))

    status = 400 if request.method == "POST" else 200
    return render_template("settings/password.html", form=form), status
