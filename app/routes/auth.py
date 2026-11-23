from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import limiter
from app.forms import LoginForm, RegistrationForm
from app.services import auth_service
from app.sockets.helpers import disconnect_user
from app.utils import safe_next_url

bp = Blueprint("auth", __name__)


def _account_key() -> str:
    """Rate-limit bucket per targeted account, so spreading an attack over many
    IP addresses still hits a per-account ceiling."""
    return "login-account:" + request.form.get("identifier", "").strip().lower()[:254]


def _failed(response) -> bool:
    return response.status_code >= 400


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["REGISTER_LIMIT"], methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            user = auth_service.register_user(
                username=form.username.data,
                email=form.email.data,
                display_name=form.display_name.data,
                password=form.password.data,
            )
        except auth_service.DuplicateAccount as exc:
            # Lost a race with another signup between validation and insert.
            getattr(form, exc.field).errors.append("That value is already in use.")
        else:
            session.clear()
            login_user(user)
            flash(f"Welcome, {user.display_name}! Your account is ready.", "success")
            return redirect(url_for("main.index"))

    status = 400 if request.method == "POST" else 200
    return render_template("auth/register.html", form=form), status


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["LOGIN_LIMIT_PER_IP"],
    methods=["POST"],
    deduct_when=_failed,
)
@limiter.limit(
    lambda: current_app.config["LOGIN_LIMIT_PER_ACCOUNT"],
    methods=["POST"],
    key_func=_account_key,
    deduct_when=_failed,
)
def login():
    next_url = safe_next_url(request.args.get("next"))
    if current_user.is_authenticated:
        return redirect(next_url or url_for("main.index"))

    form = LoginForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            return render_template("auth/login.html", form=form, next_url=next_url), 400
        user = auth_service.authenticate(form.identifier.data, form.password.data)
        if user is None:
            # One generic message: never reveal which half was wrong.
            flash("Incorrect username/email or password.", "danger")
            return render_template("auth/login.html", form=form, next_url=next_url), 401
        session.clear()
        login_user(user, remember=form.remember.data)
        return redirect(next_url or url_for("main.index"))

    return render_template("auth/login.html", form=form, next_url=next_url)


@bp.post("/logout")
@login_required
def logout():
    user_id = current_user.id
    logout_user()
    disconnect_user(user_id)  # still-valid sessions reconnect; this one cannot
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
