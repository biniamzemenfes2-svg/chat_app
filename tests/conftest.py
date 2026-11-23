import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.exit(
        "Set TEST_DATABASE_URL to a throwaway PostgreSQL database "
        "(its tables are dropped by the tests).",
        returncode=2,
    )

from flask.testing import FlaskClient  # noqa: E402
from flask_socketio.test_client import SocketIOTestClient  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models import User  # noqa: E402


class FreshContextClient(FlaskClient):
    """Run every request in its own application context, like production.

    The ``db`` fixture keeps one app context open for the whole test, and Flask
    would reuse it for every request: ``flask.g`` (which caches the logged-in
    user) and the SQLAlchemy session would then leak between requests and
    between supposedly separate browsers, hiding real authentication bugs.
    """

    def open(self, *args, **kwargs):
        with self.application.app_context():
            return super().open(*args, **kwargs)


class IsolatedSocketClient(SocketIOTestClient):
    """Socket.IO test client whose every event runs in a fresh app context
    (same reasoning as ``FreshContextClient``): login state and the database
    session must not leak between events or between clients."""

    def connect(self, *args, **kwargs):
        with self.app.app_context():
            return super().connect(*args, **kwargs)

    def emit(self, *args, **kwargs):
        with self.app.app_context():
            return super().emit(*args, **kwargs)

    def disconnect(self, *args, **kwargs):
        with self.app.app_context():
            return super().disconnect(*args, **kwargs)


@pytest.fixture(scope="session")
def app():
    from app.extensions import limiter

    application = create_app("testing")
    application.test_client_class = FreshContextClient
    limiter.enabled = False  # tests opt in with the ``rate_limits`` fixture
    return application


@pytest.fixture()
def db(app):
    """A clean schema for every test."""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(autouse=True)
def upload_folder(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    return tmp_path / "uploads"


@pytest.fixture()
def client(app, db):
    return app.test_client()


@pytest.fixture()
def rate_limits(app):
    """Enable the (normally disabled) rate limiter for one test."""
    from app.extensions import limiter

    limiter.enabled = True
    limiter.reset()
    yield
    limiter.reset()
    limiter.enabled = False


@pytest.fixture(autouse=True)
def reset_realtime_state():
    from app.services.presence import tracker
    from app.utils import socket_limiter

    tracker.reset()
    socket_limiter.reset()
    yield
    tracker.reset()
    socket_limiter.reset()


@pytest.fixture()
def connect_socket(app):
    """connect_socket(user) -> (socket client, http client) signed in as user."""
    from app.extensions import socketio

    opened = []

    def _connect(user):
        http = app.test_client()
        sign_in(http, user)
        sio = IsolatedSocketClient(app, socketio, flask_test_client=http)
        opened.append(sio)
        return sio, http

    yield _connect
    for sio in opened:
        if sio.is_connected():
            sio.disconnect()


def events(sio, name=None):
    """Received events as (name, payload) pairs, optionally only ``name``."""
    received = [(item["name"], item["args"][0] if item["args"] else None)
                for item in sio.get_received()]
    return [item for item in received if name is None or item[0] == name]


def sign_in(client, user):
    """Put a signed-in session on ``client`` without going through the form."""
    with client.session_transaction() as session:
        session["_user_id"] = user.get_id()
        session["_fresh"] = True


@pytest.fixture()
def make_user(db):
    def _make(username="alice", email=None, password="correct-horse-battery"):
        user = User(
            username=username,
            email=email or f"{username}@example.com",
            display_name=username.title(),
        )
        user.set_password(password)
        _db.session.add(user)
        _db.session.commit()
        return user

    return _make
