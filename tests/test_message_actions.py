import json

import pytest
from sqlalchemy import event

from app.models import Message, MessageReaction
from app.services import conversation_service as convs
from app.services import emoji
from app.services import message_service as msgs
from tests.conftest import events, sign_in


@pytest.fixture()
def trio(db, make_user):
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    conversation, _ = convs.get_or_create_private(alice, bob)
    return alice, bob, carol, conversation


def post(conversation, sender, body="hello", **kwargs):
    message, _ = msgs.send_message(conversation, sender, body, **kwargs)
    return message


# ===================================================================== catalog

def test_emoji_catalog_is_well_formed():
    catalog = json.loads(emoji.CATALOG_PATH.read_text(encoding="utf-8"))
    flat = [e for g in catalog["groups"] for e, _ in g["emojis"]]
    assert len(flat) == len(set(flat)) > 1500
    assert all(g["emojis"] and g["name"] and g["icon"] for g in catalog["groups"])
    for quick in ("👍", "❤️", "😂", "😮", "😢", "🙏"):
        assert emoji.is_valid_emoji(quick)


@pytest.mark.parametrize("value", ["hello", "", "👍👍", "👍 ", None, 5, ["👍"], "<script>", "\u200d"])
def test_non_catalog_values_are_not_emoji(value):
    assert not emoji.is_valid_emoji(value)


# ====================================================================== replies

def test_reply_is_quoted_in_the_payload(db, trio):
    alice, bob, _, conversation = trio
    original = post(conversation, alice, "What time is the match?")
    reply = post(conversation, bob, "Eight o'clock", reply_to_id=original.id)
    payload = msgs.serialize(reply)
    assert payload["reply_to"] == {
        "id": original.id, "sender_id": alice.id,
        "text": "What time is the match?", "deleted": False,
    }
    assert msgs.serialize(original)["reply_to"] is None


@pytest.mark.parametrize("bad", [999999, True, "1", 1.5, [1], {"id": 1}, 0, -1])
def test_replying_to_nothing_valid_is_rejected(db, trio, bad):
    alice, bob, _, conversation = trio
    post(conversation, alice, "x")
    with pytest.raises(msgs.MessageError):
        msgs.send_message(conversation, bob, "reply", reply_to_id=bad)


def test_cannot_reply_across_conversations(db, trio):
    alice, bob, carol, conversation = trio
    other, _ = convs.get_or_create_private(alice, carol)
    foreign = post(other, alice, "private to carol")
    with pytest.raises(msgs.MessageError):
        msgs.send_message(conversation, bob, "sneaky", reply_to_id=foreign.id)


def test_cannot_reply_to_a_deleted_message_and_quote_hides_deleted_text(db, trio):
    alice, bob, _, conversation = trio
    original = post(conversation, alice, "oops secret")
    reply = post(conversation, bob, "re", reply_to_id=original.id)
    msgs.delete_message(original, alice)
    with pytest.raises(msgs.MessageError):
        msgs.send_message(conversation, bob, "again", reply_to_id=original.id)
    quote = msgs.serialize(reply)["reply_to"]
    assert quote["deleted"] is True and quote["text"] == ""
    assert "oops secret" not in json.dumps(msgs.serialize(reply))


def test_quote_never_leaks_a_message_from_another_conversation(db, trio):
    """Even if a bad row existed, serialization only quotes same-conversation targets."""
    alice, bob, carol, conversation = trio
    other, _ = convs.get_or_create_private(alice, carol)
    foreign = post(other, alice, "carol-only")
    message = post(conversation, bob, "x")
    message.reply_to_id = foreign.id  # forced, bypassing the service check
    db.session.commit()
    assert msgs.serialize(message)["reply_to"] is None


# ======================================================================== edits

def test_owner_can_edit_and_it_is_marked(db, trio):
    alice, _, _, conversation = trio
    message = post(conversation, alice, "teh")
    assert msgs.edit_message(message, alice, "the") is True
    payload = msgs.serialize(message)
    assert payload["body"] == "the" and payload["edited_at"]


def test_editing_to_identical_text_is_a_noop(db, trio):
    alice, _, _, conversation = trio
    message = post(conversation, alice, "same")
    assert msgs.edit_message(message, alice, "  same  ") is False
    assert msgs.serialize(message)["edited_at"] is None


def test_only_the_sender_can_edit(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "mine")
    with pytest.raises(msgs.MessageError, match="your own"):
        msgs.edit_message(message, bob, "hijacked")
    assert db.session.get(Message, message.id).body == "mine"


@pytest.mark.parametrize("body", ["", "   ", None, 5, "x" * 4001, "a\x00b"])
def test_edit_applies_the_same_validation_as_send(db, trio, body):
    alice, _, _, conversation = trio
    message = post(conversation, alice, "original")
    with pytest.raises(msgs.MessageError):
        msgs.edit_message(message, alice, body)
    assert db.session.get(Message, message.id).body == "original"


def test_deleted_messages_cannot_be_edited(db, trio):
    alice, _, _, conversation = trio
    message = post(conversation, alice, "bye")
    msgs.delete_message(message, alice)
    with pytest.raises(msgs.MessageError, match="deleted"):
        msgs.edit_message(message, alice, "back again")


# ====================================================================== deletes

def test_delete_erases_the_text_from_the_database(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "a secret I regret")
    msgs.toggle_reaction(message, bob, "👍")
    assert msgs.delete_message(message, alice) is True

    db.session.expire_all()
    row = db.session.get(Message, message.id)
    assert row.deleted_at is not None and row.body == ""  # gone, not just hidden
    assert db.session.query(MessageReaction).count() == 0
    payload = msgs.serialize(row)
    assert payload["deleted"] and payload["body"] is None and payload["reactions"] == []
    assert msgs.delete_message(row, alice) is False  # idempotent


def test_only_the_sender_can_delete(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "keep me")
    with pytest.raises(msgs.MessageError, match="your own"):
        msgs.delete_message(message, bob)
    assert db.session.get(Message, message.id).deleted_at is None


def test_deleted_messages_leave_search_and_unread_counts(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "findable text")
    assert convs.list_for_user(bob.id)[0]["unread"] == 1
    msgs.delete_message(message, alice)
    assert convs.list_for_user(bob.id)[0]["unread"] == 0
    assert msgs.search_messages(conversation.id, "findable") == []


# =================================================================== reactions

def test_reaction_lifecycle_add_change_remove(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "react to me")
    assert msgs.toggle_reaction(message, bob, "👍") == "added"
    assert msgs.toggle_reaction(message, bob, "❤️") == "changed"  # one per user
    assert db.session.query(MessageReaction).count() == 1
    assert msgs.serialize(message)["reactions"] == [{"emoji": "❤️", "user_ids": [bob.id]}]
    assert msgs.toggle_reaction(message, bob, "❤️") == "removed"
    assert msgs.serialize(message)["reactions"] == []


def test_reactions_group_by_emoji_in_order(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "popular")
    msgs.toggle_reaction(message, bob, "😂")
    msgs.toggle_reaction(message, alice, "😂")
    assert msgs.serialize(message)["reactions"] == [{"emoji": "😂", "user_ids": [bob.id, alice.id]}]


@pytest.mark.parametrize("bad", ["lol", "", "👍👍", None, 7, "<b>", ["👍"]])
def test_invalid_reactions_are_rejected(db, trio, bad):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "x")
    with pytest.raises(msgs.MessageError):
        msgs.toggle_reaction(message, bob, bad)
    assert db.session.query(MessageReaction).count() == 0


def test_cannot_react_to_a_deleted_message(db, trio):
    alice, bob, _, conversation = trio
    message = post(conversation, alice, "x")
    msgs.delete_message(message, alice)
    with pytest.raises(msgs.MessageError):
        msgs.toggle_reaction(message, bob, "👍")


def test_member_lookup_hides_foreign_messages(db, trio):
    alice, bob, carol, conversation = trio
    message = post(conversation, alice, "x")
    assert msgs.get_message_for_member(message.id, bob.id).id == message.id
    assert msgs.get_message_for_member(message.id, carol.id) is None
    assert msgs.get_message_for_member(999999, alice.id) is None


def test_serialization_uses_a_constant_number_of_queries(db, trio):
    alice, bob, _, conversation = trio
    first = post(conversation, alice, "q")
    for i in range(25):
        message = post(conversation, bob, f"m{i}", reply_to_id=first.id)
        msgs.toggle_reaction(message, alice, "👍")
    conversation_id, bob_id = conversation.id, bob.id  # read before expiring
    db.session.expire_all()

    statements = []
    listener = lambda conn, cursor, statement, *a: statements.append(statement)  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        page = msgs.history_page(conversation_id, bob_id, limit=26)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert len(page.messages) == 26
    assert len(statements) == 3, statements  # the page, its reactions, its reply targets


# ============================================================ windows / search

@pytest.fixture()
def busy(db, trio):
    alice, bob, _, conversation = trio
    rows = [post(conversation, alice if i % 2 else bob, f"message {i:03d}") for i in range(100)]
    return alice, bob, conversation, rows


def test_window_around_a_message(db, busy):
    alice, bob, conversation, rows = busy
    page = msgs.history_page(conversation.id, bob.id, around_id=rows[50].id, limit=20)
    bodies = [m["body"] for m in page.messages]
    assert bodies == [f"message {i:03d}" for i in range(40, 60)]
    assert page.has_older and page.has_newer
    assert rows[50].id in [m["id"] for m in page.messages]


def test_window_near_the_edges_reports_no_more(db, busy):
    alice, bob, conversation, rows = busy
    start = msgs.history_page(conversation.id, bob.id, around_id=rows[0].id, limit=20)
    assert not start.has_older and start.has_newer and start.messages[0]["id"] == rows[0].id
    end = msgs.history_page(conversation.id, bob.id, around_id=rows[-1].id, limit=20)
    assert end.has_older and not end.has_newer and end.messages[-1]["id"] == rows[-1].id


def test_window_rejects_messages_of_other_conversations(db, trio, busy):
    alice, bob, conversation, rows = busy
    carol = trio[2]
    other, _ = convs.get_or_create_private(alice, carol)
    foreign = post(other, alice, "elsewhere")
    assert msgs.history_page(conversation.id, bob.id, around_id=foreign.id) is None
    assert msgs.history_page(conversation.id, bob.id, around_id=999999) is None


def test_forward_paging(db, busy):
    alice, bob, conversation, rows = busy
    page = msgs.history_page(conversation.id, bob.id, after_id=rows[89].id, limit=5)
    assert [m["body"] for m in page.messages] == [f"message {i:03d}" for i in range(90, 95)]
    assert page.has_newer
    last = msgs.history_page(conversation.id, bob.id, after_id=rows[94].id, limit=50)
    assert len(last.messages) == 5 and not last.has_newer


def test_search_is_case_insensitive_newest_first_and_scoped(db, trio):
    alice, bob, carol, conversation = trio
    other, _ = convs.get_or_create_private(alice, carol)
    post(conversation, alice, "Let's meet at the CAFE")
    post(conversation, bob, "which cafe?")
    post(other, alice, "cafe talk with carol")
    hits = msgs.search_messages(conversation.id, "cafe")
    assert [h["body"] for h in hits] == ["which cafe?", "Let's meet at the CAFE"]


def test_search_wildcards_are_literal_and_short_queries_ignored(db, trio):
    alice, _, _, conversation = trio
    post(conversation, alice, "100% sure")
    post(conversation, alice, "100 percent")
    post(conversation, alice, "snake_case")
    post(conversation, alice, "snakeXcase")
    assert [h["body"] for h in msgs.search_messages(conversation.id, "100%")] == ["100% sure"]
    assert [h["body"] for h in msgs.search_messages(conversation.id, "ke_ca")] == ["snake_case"]
    assert msgs.search_messages(conversation.id, "%") == []
    assert msgs.search_messages(conversation.id, "a") == []
    assert msgs.search_messages(conversation.id, "   ") == []


def test_search_result_limit(db, trio):
    alice, _, _, conversation = trio
    for i in range(40):
        post(conversation, alice, f"needle {i}")
    assert len(msgs.search_messages(conversation.id, "needle")) == msgs.SEARCH_LIMIT


# ================================================================ HTTP endpoints

def http(app, user):
    client = app.test_client()
    sign_in(client, user)
    return client


def test_messages_endpoint_around_after_and_authorization(app, db, trio, busy):
    alice, bob, conversation, rows = busy
    carol = trio[2]
    client = http(app, bob)
    around = client.get(f"/api/conversations/{conversation.id}/messages?around={rows[50].id}&limit=10").get_json()
    assert rows[50].id in [m["id"] for m in around["messages"]] and around["has_newer"] and around["has_more"]
    after = client.get(f"/api/conversations/{conversation.id}/messages?after={rows[96].id}").get_json()
    assert [m["body"] for m in after["messages"]] == ["message 097", "message 098", "message 099"]
    assert after["has_newer"] is False

    thief = http(app, carol)
    assert thief.get(f"/api/conversations/{conversation.id}/messages?around={rows[50].id}").status_code == 404
    other, _ = convs.get_or_create_private(bob, carol)
    foreign = post(other, bob, "x")
    assert client.get(f"/api/conversations/{conversation.id}/messages?around={foreign.id}").status_code == 404


def test_search_endpoint(app, db, trio):
    alice, bob, carol, conversation = trio
    post(conversation, alice, "the quick brown fox")
    client = http(app, bob)
    ok = client.get(f"/api/conversations/{conversation.id}/search?q=BROWN").get_json()
    assert [m["body"] for m in ok["messages"]] == ["the quick brown fox"]
    assert client.get(f"/api/conversations/{conversation.id}/search?q=x").get_json() == {"messages": []}

    assert http(app, carol).get(f"/api/conversations/{conversation.id}/search?q=brown").status_code == 404
    assert app.test_client().get(f"/api/conversations/{conversation.id}/search?q=brown").status_code == 401


def test_search_endpoint_is_rate_limited(app, db, trio, rate_limits):
    alice, bob, _, conversation = trio
    client = http(app, bob)
    url = f"/api/conversations/{conversation.id}/search?q=hello"
    for _ in range(60):
        assert client.get(url).status_code == 200
    assert client.get(url).status_code == 429


# ================================================================== sockets

def sock_send(sio, conversation_id, body="hi", **extra):
    return sio.emit("send_message", {"conversation_id": conversation_id, "body": body, **extra}, callback=True)


def drain(*clients):
    for client in clients:
        client.get_received()


def test_reply_over_socket_includes_the_quote(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    original = sock_send(a, conversation.id, "ping")["message"]
    drain(a, b)
    ack = sock_send(b, conversation.id, "pong", reply_to_id=original["id"])
    assert ack["ok"] and ack["message"]["reply_to"]["text"] == "ping"
    assert events(a, "message_new")[0][1]["reply_to"]["id"] == original["id"]


@pytest.mark.parametrize("bad", [999999, "1", True, [1]])
def test_socket_rejects_bad_reply_targets(db, trio, connect_socket, bad):
    alice, _, _, conversation = trio
    a, _ = connect_socket(alice)
    ack = sock_send(a, conversation.id, "x", reply_to_id=bad)
    assert ack["ok"] is False and db.session.query(Message).count() == 0


def test_socket_reply_cannot_quote_another_conversation(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    other, _ = convs.get_or_create_private(alice, carol)
    secret = post(other, alice, "for carol only")
    b, _ = connect_socket(bob)
    ack = sock_send(b, conversation.id, "gotcha", reply_to_id=secret.id)
    assert ack["ok"] is False
    assert "for carol only" not in json.dumps(ack)


def test_edit_is_broadcast_to_both_participants(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    message = sock_send(a, conversation.id, "helo")["message"]
    drain(a, b, c)

    ack = a.emit("edit_message", {"message_id": message["id"], "body": "hello"}, callback=True)
    assert ack["ok"] and ack["message"]["body"] == "hello" and ack["message"]["edited_at"]
    for client in (a, b):
        updates = events(client, "message_updated")
        assert len(updates) == 1 and updates[0][1]["body"] == "hello"
    assert events(c) == []


def test_edit_authorization(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    mid = sock_send(a, conversation.id, "original")["message"]["id"]
    drain(a, b, c)

    peer = b.emit("edit_message", {"message_id": mid, "body": "forged"}, callback=True)
    stranger = c.emit("edit_message", {"message_id": mid, "body": "forged"}, callback=True)
    missing = c.emit("edit_message", {"message_id": 999999, "body": "x"}, callback=True)
    assert peer == {"ok": False, "error": "You can only edit your own messages."}
    assert stranger == missing == {"ok": False, "error": "Message not found."}
    assert db.session.get(Message, mid).body == "original"
    assert events(a) == [] and events(b) == []


def test_delete_is_broadcast_and_erases_text(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    mid = sock_send(a, conversation.id, "delete me")["message"]["id"]
    drain(a, b)
    ack = a.emit("delete_message", {"message_id": mid}, callback=True)
    assert ack["ok"] and ack["message"]["deleted"] is True and ack["message"]["body"] is None
    update = events(b, "message_updated")[0][1]
    assert update["deleted"] and update["body"] is None
    db.session.expire_all()
    assert db.session.get(Message, mid).body == ""


def test_delete_authorization(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    mid = sock_send(a, conversation.id, "mine")["message"]["id"]
    assert b.emit("delete_message", {"message_id": mid}, callback=True)["ok"] is False
    assert c.emit("delete_message", {"message_id": mid}, callback=True) == {"ok": False, "error": "Message not found."}
    assert db.session.get(Message, mid).deleted_at is None


def test_reaction_flow_over_sockets(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    b, _ = connect_socket(bob)
    c, _ = connect_socket(carol)
    mid = sock_send(a, conversation.id, "react")["message"]["id"]
    drain(a, b, c)

    ack = b.emit("react", {"message_id": mid, "emoji": "🙏"}, callback=True)
    assert ack["ok"] and ack["outcome"] == "added"
    assert events(a, "message_updated")[0][1]["reactions"] == [{"emoji": "🙏", "user_ids": [bob.id]}]
    assert events(c) == []
    assert b.emit("react", {"message_id": mid, "emoji": "🙏"}, callback=True)["outcome"] == "removed"
    assert events(a, "message_updated")[0][1]["reactions"] == []


def test_reaction_authorization_and_validation(db, trio, connect_socket):
    alice, bob, carol, conversation = trio
    a, _ = connect_socket(alice)
    c, _ = connect_socket(carol)
    mid = sock_send(a, conversation.id, "x")["message"]["id"]
    assert c.emit("react", {"message_id": mid, "emoji": "👍"}, callback=True) == {"ok": False, "error": "Message not found."}
    for bad in ("nope", "", None, 5, ["👍"], "👍👍", "<img>"):
        assert a.emit("react", {"message_id": mid, "emoji": bad}, callback=True)["ok"] is False
    assert db.session.query(MessageReaction).count() == 0


@pytest.mark.parametrize("payload", [None, "x", 5, [], {}, {"message_id": "1"}, {"message_id": True}, {"message_id": 1.5}, {"message_id": None}])
@pytest.mark.parametrize("event_name", ["edit_message", "delete_message", "react"])
def test_malformed_action_payloads(db, trio, connect_socket, event_name, payload):
    alice, _, _, _ = trio
    a, _ = connect_socket(alice)
    ack = a.emit(event_name, payload, callback=True)
    assert ack["ok"] is False and a.is_connected()


def test_reactions_are_rate_limited(db, trio, connect_socket):
    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    mid = sock_send(a, conversation.id, "x")["message"]["id"]
    results = [a.emit("react", {"message_id": mid, "emoji": "👍"}, callback=True)["ok"] for _ in range(35)]
    assert results.count(True) == 30 and results[-1] is False


def test_revoked_session_cannot_edit_delete_or_react(db, trio, connect_socket):
    from app.models import User

    alice, bob, _, conversation = trio
    a, _ = connect_socket(alice)
    mid = sock_send(a, conversation.id, "x")["message"]["id"]
    db.session.get(User, alice.id).rotate_session_token()
    db.session.commit()
    ack = a.emit("delete_message", {"message_id": mid}, callback=True)
    assert ack["ok"] is False and not a.is_connected()
    assert db.session.get(Message, mid).deleted_at is None
