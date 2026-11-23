import pytest

from app.models import Message, MessageRead, User
from app.services import conversation_service as convs
from app.services import message_service as msgs
from tests.conftest import events, sign_in

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def trio(db, make_user):
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    conversation, _ = convs.get_or_create_private(alice, bob)
    return alice, bob, carol, conversation


def send(sio, conversation_id, body="hello", **extra):
    return sio.emit(
        "send_message",
        {"conversation_id": conversation_id, "body": body, **extra},
        callback=True,
    )


def drain(*clients):
    for client in clients:
        client.get_received()


# --------------------------------------------------------------- sending

def test_message_reaches_both_participants_and_is_stored(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    drain(a, b, c)

    ack = send(a, conversation.id, "hi bob", client_id="client-0001")
    assert ack["ok"] and ack["message"]["body"] == "hi bob"
    assert ack["message"]["sender_id"] == alice.id
    assert ack["message"]["client_id"] == "client-0001"

    to_bob = events(b, "message_new")
    to_alice = events(a, "message_new")
    assert [p["id"] for _, p in to_bob] == [ack["message"]["id"]]
    assert [p["id"] for _, p in to_alice] == [ack["message"]["id"]]  # her other tabs
    assert events(c) == []  # a stranger hears nothing

    stored = db.session.query(Message).one()
    assert stored.body == "hi bob" and stored.conversation_id == conversation.id


def test_delivered_flag_depends_on_recipient_being_online(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    offline = send(a, conversation.id, "are you there?")
    assert offline["message"]["delivered_at"] is None

    b, _ = connect_socket(bob)
    online = send(a, conversation.id, "now you are")
    assert online["message"]["delivered_at"] is not None


def test_non_participant_cannot_send_into_a_conversation(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    drain(a, b, c)

    forged = send(c, conversation.id, "I'm not in this chat")
    missing = send(c, 999999, "no such chat")
    assert forged == missing == {"ok": False, "error": "Conversation not found."}
    assert db.session.query(Message).count() == 0
    assert events(a) == [] and events(b) == []


@pytest.mark.parametrize(
    "payload",
    [
        None, "text", 42, [], [1, 2],
        {}, {"body": "no conversation"},
        {"conversation_id": "1", "body": "string id"},
        {"conversation_id": True, "body": "bool id"},
        {"conversation_id": 1.5, "body": "float id"},
        {"conversation_id": None, "body": "null id"},
        {"conversation_id": {"$gt": 0}, "body": "operator injection"},
        {"conversation_id": [1], "body": "list id"},
    ],
)
def test_malformed_send_payloads_are_rejected_cleanly(db, trio, connect_socket, payload):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    if isinstance(payload, dict) and payload.get("conversation_id") == 1.5:
        pass
    ack = a.emit("send_message", payload, callback=True)
    assert ack["ok"] is False
    assert db.session.query(Message).count() == 0
    assert a.is_connected()  # the server didn't choke


@pytest.mark.parametrize("body", [None, "", "   ", 123, ["x"], {"a": 1}, "x" * 4001, "nul\x00byte"])
def test_invalid_bodies_are_rejected(db, trio, connect_socket, body):
    alice, _, _, conversation = trio
    a, _ = connect_socket(alice)
    ack = a.emit("send_message", {"conversation_id": conversation.id, "body": body}, callback=True)
    assert ack["ok"] is False and ack["error"]
    assert db.session.query(Message).count() == 0


def test_message_content_is_stored_and_sent_verbatim_not_html_escaped(db, trio, connect_socket):
    """The client renders with textContent, so the server must not mangle text."""
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    drain(a, b)
    payload = '<img src=x onerror="alert(1)"> & "quotes" ሰላም 👋'
    send(a, conversation.id, payload)
    assert events(b, "message_new")[0][1]["body"] == payload


def test_retrying_with_the_same_client_id_never_duplicates(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    first = send(a, conversation.id, "once", client_id="retry-000001")
    drain(a, b)
    second = send(a, conversation.id, "once", client_id="retry-000001")

    assert first["message"]["id"] == second["message"]["id"] and second["ok"]
    assert db.session.query(Message).count() == 1
    assert events(b) == []  # no second broadcast


def test_sending_is_rate_limited_per_user(app, db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    app.config["SOCKET_MESSAGES_PER_WINDOW"] = 3
    try:
        results = [send(a, conversation.id, f"m{i}")["ok"] for i in range(5)]
        assert results == [True, True, True, False, False]
        assert "too quickly" in send(a, conversation.id, "again")["error"]
        assert send(b, conversation.id, "bob is unaffected")["ok"]
    finally:
        app.config["SOCKET_MESSAGES_PER_WINDOW"] = 20
    assert db.session.query(Message).count() == 4


# ---------------------------------------------------------------- typing

def test_typing_goes_only_to_the_other_participant(trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    drain(a, b, c)

    a.emit("typing", {"conversation_id": conversation.id, "typing": True})
    assert events(b, "typing") == [
        ("typing", {"conversation_id": conversation.id, "user_id": alice.id, "typing": True})
    ]
    assert events(a) == [] and events(c) == []

    a.emit("typing", {"conversation_id": conversation.id, "typing": False})
    assert events(b, "typing")[0][1]["typing"] is False


def test_typing_into_a_foreign_conversation_is_ignored(trio, connect_socket):
    alice, bob, carol, conversation = trio
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    drain(b, c)
    c.emit("typing", {"conversation_id": conversation.id, "typing": True})
    assert events(b) == []


@pytest.mark.parametrize("payload", [None, "x", 5, {}, {"conversation_id": "1"}, {"conversation_id": [1]}])
def test_malformed_typing_payloads_are_ignored(trio, connect_socket, payload):
    alice, bob, _, _ = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    drain(a, b)
    a.emit("typing", payload)
    assert events(b) == [] and a.is_connected()


def test_typing_events_are_throttled(trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    drain(a, b)
    for _ in range(100):
        a.emit("typing", {"conversation_id": conversation.id, "typing": True})
    assert len(events(b, "typing")) == 30


# ------------------------------------------------------------- read receipts

def test_mark_read_notifies_sender_and_clears_unread(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    first = send(a, conversation.id, "1")["message"]["id"]
    second = send(a, conversation.id, "2")["message"]["id"]
    drain(a, b)

    ack = b.emit("mark_read", {"conversation_id": conversation.id}, callback=True)
    assert ack == {"ok": True, "count": 2}

    read = events(a, "messages_read")
    assert len(read) == 1
    assert read[0][1] == {
        "conversation_id": conversation.id, "reader_id": bob.id,
        "message_ids": sorted([first, second]),
    } or sorted(read[0][1]["message_ids"]) == sorted([first, second])
    assert events(b, "conversation_read") == [("conversation_read", {"conversation_id": conversation.id})]
    assert convs.list_for_user(bob.id)[0]["unread"] == 0

    again = b.emit("mark_read", {"conversation_id": conversation.id}, callback=True)
    assert again == {"ok": True, "count": 0} and events(a) == []


def test_mark_read_requires_membership(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    c, _ = connect_socket(carol)
    send(a, conversation.id, "secret")
    ack = c.emit("mark_read", {"conversation_id": conversation.id}, callback=True)
    assert ack == {"ok": False, "error": "Conversation not found."}
    assert db.session.query(MessageRead).count() == 0


@pytest.mark.parametrize("payload", [None, "x", {}, {"conversation_id": "1"}, {"conversation_id": False}])
def test_malformed_mark_read_payloads(trio, connect_socket, payload):
    alice, _, _, _ = trio
    a, _ = connect_socket(alice)
    assert a.emit("mark_read", payload, callback=True)["ok"] is False


def test_messages_sent_while_offline_are_delivered_on_connect(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    sent = send(a, conversation.id, "while you were away")["message"]
    assert sent["delivered_at"] is None
    drain(a)

    connect_socket(bob)
    delivered = events(a, "messages_delivered")
    assert delivered == [("messages_delivered", {"conversation_id": conversation.id, "message_ids": [sent["id"]]})]
    db.session.expire_all()
    assert db.session.get(Message, sent["id"]).delivered_at is not None


# ---------------------------------------------------------------- presence

def test_presence_is_shared_with_conversation_peers_only(db, trio, connect_socket):
    alice, bob, carol, _ = trio
    a, _ = connect_socket(alice)
    c, _ = connect_socket(carol)
    drain(a, c)

    b, _ = connect_socket(bob)
    assert events(a, "presence") == [("presence", {"user_id": bob.id, "online": True})]
    assert events(c, "presence") == []  # carol shares no conversation with bob


def test_new_connection_learns_who_is_already_online(trio, connect_socket):
    alice, bob, carol, _ = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    state = events(b, "presence_state")
    assert state == [("presence_state", {"online": [alice.id]})]
    c, _ = connect_socket(carol)
    assert events(c, "presence_state") == [("presence_state", {"online": []})]


def test_multiple_tabs_count_as_one_presence(db, trio, connect_socket):
    alice, bob, _, _ = trio
    a, _ = connect_socket(alice)
    b1, _ = connect_socket(bob)
    drain(a)
    b2, _ = connect_socket(bob)
    assert events(a, "presence") == []  # second tab: nothing new to say

    b1.disconnect()
    assert events(a, "presence") == []  # still online via the other tab
    b2.disconnect()
    gone = events(a, "presence")
    assert len(gone) == 1 and gone[0][1]["online"] is False and gone[0][1]["user_id"] == bob.id
    assert gone[0][1]["last_seen"]


def test_going_offline_records_last_seen(db, trio, connect_socket):
    alice, bob, _, _ = trio
    assert bob.last_seen is None
    b, _ = connect_socket(bob)
    b.disconnect()
    db.session.expire_all()
    assert db.session.get(User, bob.id).last_seen is not None


def test_presence_tracker_matches_connections(trio, connect_socket):
    from app.services.presence import tracker

    alice, bob, _, _ = trio
    a, _ = connect_socket(alice)
    assert tracker.is_online(alice.id) and not tracker.is_online(bob.id)
    a.disconnect()
    assert not tracker.is_online(alice.id)


# --------------------------------------------------- revoked / ended sessions

def test_stale_connection_is_rejected_on_next_event(db, trio, connect_socket):
    """A socket opened before a password change must stop working at once."""
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)

    user = db.session.get(User, alice.id)
    user.rotate_session_token()  # what a password change does
    db.session.commit()
    drain(b)

    ack = send(a, conversation.id, "from a revoked session")
    assert ack["ok"] is False and "sign in" in ack["error"].lower()
    assert not a.is_connected()
    assert db.session.query(Message).count() == 0
    assert events(b, "message_new") == []


def test_deactivated_users_connection_stops_working(db, trio, connect_socket):
    alice, _, _, conversation = trio
    a, _ = connect_socket(alice)
    db.session.get(User, alice.id).is_active = False
    db.session.commit()
    assert send(a, conversation.id, "hello?")["ok"] is False
    assert not a.is_connected()


def test_revoked_session_cannot_open_a_new_connection(db, trio, connect_socket, app):
    from app.extensions import socketio
    from tests.conftest import IsolatedSocketClient

    alice, _, _, _ = trio
    http = app.test_client()
    sign_in(http, alice)
    db.session.get(User, alice.id).rotate_session_token()
    db.session.commit()
    sio = IsolatedSocketClient(app, socketio, flask_test_client=http)
    assert not sio.is_connected()


def test_password_change_closes_live_connections_on_every_device(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    laptop, laptop_http = connect_socket(alice)
    phone, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    drain(b)

    response = laptop_http.post(
        "/settings/password",
        data={
            "current_password": PASSWORD,
            "new_password": "a-completely-new-passphrase",
            "confirm_new_password": "a-completely-new-passphrase",
        },
    )
    assert response.status_code == 302
    assert not phone.is_connected()   # the other device is cut off...
    assert not laptop.is_connected()  # ...and this one reconnects with its fresh cookie
    from app.services.presence import tracker
    assert not tracker.is_online(alice.id)

    # The stolen/old session can't get back in.
    from app.extensions import socketio
    from tests.conftest import IsolatedSocketClient
    stale_http = laptop_http.application.test_client()
    with stale_http.session_transaction() as session:
        session["_user_id"] = f"{alice.id}:{'0' * 32}"
    assert not IsolatedSocketClient(laptop_http.application, socketio, flask_test_client=stale_http).is_connected()


def test_logout_closes_live_connections(trio, connect_socket):
    alice, _, _, _ = trio
    a, http = connect_socket(alice)
    assert http.post("/logout").status_code == 302
    assert not a.is_connected()


def test_clients_cannot_join_arbitrary_rooms(trio, connect_socket):
    """There is no join event at all: rooms are assigned from the session."""
    alice, bob, carol, conversation = trio
    c, _ = connect_socket(carol)
    for event_name in ("join", "join_room", "join_conversation", "subscribe"):
        c.emit(event_name, {"room": f"user:{alice.id}", "conversation_id": conversation.id})
    drain(c)
    from app.extensions import socketio
    socketio.emit("secret", {"x": 1}, to=f"user:{alice.id}")
    socketio.emit("secret", {"x": 1}, to=f"user:{bob.id}")
    assert events(c) == []
