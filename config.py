"""Application configuration.

All secrets and environment-specific values come from environment variables
(optionally loaded from a local ``.env`` file, which is never committed).
"""
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger(__name__)

# Values from .env.example that must never be used for real.
_PLACEHOLDER_SECRETS = {"", "change-me", "SECRET_KEY", "changeme"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str):
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _database_url(name: str):
    url = os.environ.get(name)
    # Some hosts still hand out the legacy "postgres://" scheme.
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = False
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # Uploads (stored outside ``static/`` and served through authorized routes)
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "app" / "uploads"))

    # Rate limiting. Use a shared store (e.g. redis://...) with several workers.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Socket.IO. "threading" suits development; production uses "gevent".
    SOCKETIO_ASYNC_MODE = os.environ.get("SOCKETIO_ASYNC_MODE", "threading")
    # None = same-origin only. Set a comma-separated list to allow others.
    SOCKETIO_CORS_ORIGINS = _env_list("SOCKETIO_CORS_ORIGINS")

    # Number of reverse proxies in front of the app (for correct client IPs).
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", "0"))


class DevelopmentConfig(Config):
    DEBUG = _env_bool("FLASK_DEBUG", True)


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = _database_url("TEST_DATABASE_URL")
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def finalize_config(app, config_name: str) -> None:
    """Validate configuration and apply safe development fallbacks."""
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        needed = "TEST_DATABASE_URL" if config_name == "testing" else "DATABASE_URL"
        raise RuntimeError(
            f"{needed} is not set. Copy .env.example to .env and set it, "
            "e.g. postgresql://user:password@localhost:5432/chatapp"
        )

    secret = app.config.get("SECRET_KEY")
    if not secret or secret in _PLACEHOLDER_SECRETS:
        if config_name == "production":
            raise RuntimeError(
                "SECRET_KEY must be set to a long random value in production."
            )
        # Development only: random per-process key. Sessions reset on restart.
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("SECRET_KEY not set; using a temporary key (dev only).")
