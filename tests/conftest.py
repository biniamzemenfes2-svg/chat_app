import os

import pytest

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.exit(
        "Set TEST_DATABASE_URL to a throwaway PostgreSQL database "
        "(its tables are dropped by the tests).",
        returncode=2,
    )

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return create_app("testing")


@pytest.fixture()
def db(app):
    """A clean schema for every test."""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app, db):
    return app.test_client()


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
