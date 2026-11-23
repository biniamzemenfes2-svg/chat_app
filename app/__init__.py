"""Application factory."""
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config_by_name, finalize_config

from .extensions import csrf, db, limiter, login_manager, migrate, socketio


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

    from .routes.main import bp as main_bp

    app.register_blueprint(main_bp)

    from . import sockets  # noqa: F401  (registers Socket.IO handlers)

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    return app
