"""Application factory."""
import os

from flask import Flask, flash, jsonify, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config_by_name, finalize_config

from .extensions import csrf, db, limiter, login_manager, migrate, socketio


# No inline scripts or styles and no third-party hosts: everything is served
# from this origin. Camera/microphone streams (calls) are blob: media.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "connect-src 'self' ws: wss:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")
    if config_name not in config_by_name:
        raise RuntimeError(f"Unknown FLASK_CONFIG '{config_name}'")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])
    finalize_config(app, config_name)

    proxies = app.config["PROXY_COUNT"]
    if proxies > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies
        )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    socketio.init_app(
        app,
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        cors_allowed_origins=app.config["SOCKETIO_CORS_ORIGINS"],
    )

    # Importing registers models with SQLAlchemy and the user loader.
    from . import models  # noqa: F401

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"

    from .errors import register_error_handlers
    from .routes import auth, conversations, main, profiles, settings, users

    for module in (main, auth, profiles, settings, users, conversations):
        app.register_blueprint(module.bp)

    @login_manager.unauthorized_handler
    def unauthorized():
        # Scripts calling the JSON API need a status code, not a redirect.
        if request.path.startswith("/api/"):
            return jsonify(error="Please sign in again."), 401
        flash(login_manager.login_message, login_manager.login_message_category)
        target = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=target))
    register_error_handlers(app)

    from . import sockets  # noqa: F401  (registers Socket.IO handlers)

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    return app
