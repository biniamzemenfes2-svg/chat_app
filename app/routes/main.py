from flask import Blueprint, jsonify, render_template
from flask_login import current_user
from sqlalchemy import text

from app.extensions import db, limiter

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    if current_user.is_authenticated:
        return render_template("dashboard.html")
    return render_template("index.html")


@bp.get("/healthz")
@limiter.exempt
def healthz():
    """Liveness/readiness probe: confirms the app can reach the database."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        return jsonify(status="error", database="unreachable"), 503
    return jsonify(status="ok", database="ok")
