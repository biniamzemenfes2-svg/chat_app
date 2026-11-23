import pytest
from flask import Flask

from app.extensions import socketio
from config import finalize_config


def test_healthz_reports_database_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_index_renders(client):
    assert client.get("/").status_code == 200


def test_security_headers_present(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


def test_production_refuses_missing_secret():
    stub = Flask(__name__)
    stub.config.update(
        SQLALCHEMY_DATABASE_URI="postgresql://u:p@localhost/db", SECRET_KEY=None
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        finalize_config(stub, "production")


def test_production_refuses_placeholder_secret():
    stub = Flask(__name__)
    stub.config.update(
        SQLALCHEMY_DATABASE_URI="postgresql://u:p@localhost/db", SECRET_KEY="change-me"
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        finalize_config(stub, "production")


def test_missing_database_url_is_a_clear_error():
    stub = Flask(__name__)
    stub.config.update(SQLALCHEMY_DATABASE_URI=None, SECRET_KEY="x" * 32)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        finalize_config(stub, "development")


def test_socket_rejects_anonymous_connection(app, client):
    sio = socketio.test_client(app, flask_test_client=client)
    assert not sio.is_connected()


def test_socket_accepts_logged_in_user_and_joins_personal_room(app, client, make_user):
    user = make_user("alice")
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True

    sio = socketio.test_client(app, flask_test_client=client)
    assert sio.is_connected()

    socketio.emit("ping", {"ok": True}, to=f"user:{user.id}")
    received = sio.get_received()
    assert [event["name"] for event in received] == ["ping"]


def test_user_does_not_receive_other_users_room_events(app, client, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    with client.session_transaction() as session:
        session["_user_id"] = str(alice.id)
        session["_fresh"] = True

    sio = socketio.test_client(app, flask_test_client=client)
    socketio.emit("ping", {}, to=f"user:{bob.id}")
    assert sio.get_received() == []


def test_development_gets_a_temporary_secret_when_none_is_set():
    stub = Flask(__name__)
    stub.config.update(
        SQLALCHEMY_DATABASE_URI="postgresql://u:p@localhost/db", SECRET_KEY=None
    )
    finalize_config(stub, "development")
    assert len(stub.config["SECRET_KEY"]) >= 32
