import pytest
from sqlalchemy import event

from app.models import Message, MessageRead
from app.services import conversation_service as convs
from app.services import message_service as msgs
from tests.conftest import sign_in


@pytest.fixture()
def duo(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation, _ = convs.get_or_create_private(alice, bob)
    return alice, bob, conversation


def post(conversation, sender, body="hello", **kwargs):
    message, _ = msgs.send_message(conversation, sender, body, **kwargs)
    return message


# ------------------------------------------------------------------ cleaning

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  hello  ", "hello"),
        ("line1\r\nline2\rline3", "line1\nline2\nline3"),
        ("tabs\tare fine", "tabs\tare fine"),
        ("ሰላም 👋 مرحبا", "ሰላም 👋 مرحبا"),
        ("<script>alert(1)</script>", "<script>alert(1)</script>"),  # stored raw; clients use textContent
    ],
)
def test_clean_body_accepts_and_normalises(db, raw, expected):
    assert msgs.clean_body(raw) == expected


@pytest.mark.parametrize("raw", ["", "   \n\t ", None, 123, ["a"], {"a": 1}, "a\x00b", "bell\x07", "x" * 4001])
def test_clean_body_rejects(db, raw):
    with pytest.raises(msgs.MessageError):
        msgs.clean_body(raw)


def test_clean_body_allows_exactly_the_maximum(db):
    assert len(msgs.clean_body("x" * 4000)) == 4000


# ------------------------------------------------------------------- sending

def test_send_persists_and_bumps_conversation(db, duo):
    alice, bob, conversation = duo
    before = conversation.updated_at
    message = post(conversation, alice, "hi bob")
    db.session.refresh(conversation)
    assert message.id and message.body == "hi bob" and message.sender_id == alice.id
    assert message.delivered_at is None
    assert conversation.updated_at > before


def test_send_marks_delivered_when_asked(duo):
    alice, _, conversation = duo
    assert post(conversation, alice, delivered=True).delivered_at is not None


def test_client_id_makes_sending_idempotent(db, duo):
    alice, _, conversation = duo
    first, created1 = msgs.send_message(conversation, alice, "once", client_id="abc-12345678")
    second, created2 = msgs.send_message(conversation, alice, "once", client_id="abc-12345678")
    assert created1 and not created2 and first.id == second.id
    assert db.session.query(Message).count() == 1


def test_client_id_is_scoped_to_the_sender(db, duo):
    alice, bob, conversation = duo
    msgs.send_message(conversation, alice, "a", client_id="same-id-12345")
    _, created = msgs.send_message(conversation, bob, "b", client_id="same-id-12345")
    assert created and db.session.query(Message).count() == 2


@pytest.mark.parametrize("bad", ["short", "has space 1234", "emoji-😀-12345", "x" * 37, 12345678, ""])
def test_invalid_client_ids_are_rejected(duo, bad):
    alice, _, conversation = duo
    with pytest.raises(msgs.MessageError):
        msgs.send_message(conversation, alice, "hi", client_id=bad)


def test_lost_race_on_client_id_returns_existing(db, duo, monkeypatch):
    """Retry arrives while the first insert is committing: no 500, no duplicate."""
    alice, _, conversation = duo
    original, _ = msgs.send_message(conversation, alice, "x", client_id="race-12345678")
    lookups = {"n": 0}
    real = msgs._find_by_client_id

    def blind_once(sender_id, client_id):
        lookups["n"] += 1
        return None if lookups["n"] == 1 else real(sender_id, client_id)

    monkeypatch.setattr(msgs, "_find_by_client_id", blind_once)
    again, created = msgs.send_message(conversation, alice, "x", client_id="race-12345678")
    assert not created and again.id == original.id
    assert db.session.query(Message).count() == 1


# ------------------------------------------------------------------- history

def test_history_pages_oldest_first_with_cursor(db, duo):
    alice, bob, conversation = duo
    for i in range(120):
        post(conversation, alice if i % 2 else bob, f"m{i}")

    page1, more1 = msgs.history(conversation.id, bob.id)
    assert more1 and len(page1) == 50
    assert [m["body"] for m in page1] == [f"m{i}" for i in range(70, 120)]

    page2, more2 = msgs.history(conversation.id, bob.id, before_id=page1[0]["id"])
    assert more2 and [m["body"] for m in page2] == [f"m{i}" for i in range(20, 70)]

    page3, more3 = msgs.history(conversation.id, bob.id, before_id=page2[0]["id"])
    assert not more3 and [m["body"] for m in page3] == [f"m{i}" for i in range(20)]


@pytest.mark.parametrize("limit, expected", [(0, 1), (-5, 1), (500, 100), (3, 3)])
def test_history_clamps_limit(db, duo, limit, expected):
    alice, bob, conversation = duo
    for i in range(110):
        post(conversation, alice, f"m{i}")
    page, _ = msgs.history(conversation.id, bob.id, limit=limit)
    assert len(page) == expected


def test_history_only_returns_this_conversation(db, make_user, duo):
    alice, bob, conversation = duo
    carol = make_user("carol")
    other, _ = convs.get_or_create_private(alice, carol)
    post(conversation, alice, "for bob")
    post(other, alice, "for carol")
    page, _ = msgs.history(conversation.id, bob.id)
    assert [m["body"] for m in page] == ["for bob"]


def test_history_reports_read_state_for_the_sender(db, duo):
    alice, bob, conversation = duo
    mine = post(conversation, alice, "read me")
    post(conversation, alice, "unread")
    msgs.mark_read(conversation.id, bob.id)  # bob reads both...
    db.session.query(MessageRead).filter_by(message_id=mine.id + 1).delete()  # ...then forget the 2nd
    db.session.commit()
    page, _ = msgs.history(conversation.id, bob.id)
    assert page[0]["read_at"] and page[1]["read_at"] is None


def test_deleted_messages_hide_their_body(db, duo):
    from app.models.base import utcnow

    alice, bob, conversation = duo
    message = post(conversation, alice, "secret")
    message.deleted_at = utcnow()
    db.session.commit()
    page, _ = msgs.history(conversation.id, bob.id)
    assert page[0]["body"] is None and page[0]["deleted"] is True
    assert msgs.preview(message)["text"] == "This message was deleted"


def test_preview_is_single_line_and_truncated(duo):
    alice, _, conversation = duo
    message = post(conversation, alice, "word\n" * 60)
    text = msgs.preview(message)["text"]
    assert "\n" not in text and len(text) <= msgs.PREVIEW_LENGTH and text.endswith("…")
    assert msgs.preview(None) is None


# -------------------------------------------------------------- read/delivery

def test_mark_read_is_incremental_and_ignores_own_messages(db, duo):
    alice, bob, conversation = duo
    m1, m2 = post(conversation, alice, "1"), post(conversation, alice, "2")
    own = post(conversation, bob, "mine")

    assert sorted(msgs.mark_read(conversation.id, bob.id)) == sorted([m1.id, m2.id])
    assert msgs.mark_read(conversation.id, bob.id) == []  # idempotent
    m3 = post(conversation, alice, "3")
    assert msgs.mark_read(conversation.id, bob.id) == [m3.id]
    assert db.session.query(MessageRead).filter_by(message_id=own.id).count() == 0
    db.session.expire_all()
    assert m1.delivered_at is not None  # read implies delivered


def test_mark_delivered_for_user_groups_and_skips_own(db, make_user, duo):
    alice, bob, conversation = duo
    carol = make_user("carol")
    other, _ = convs.get_or_create_private(alice, carol)
    a1, a2 = post(conversation, alice, "1"), post(conversation, alice, "2")
    c1 = post(other, carol, "from carol")
    post(conversation, bob, "bob's own")

    result = msgs.mark_delivered_for_user(bob.id)
    assert {k: sorted(v) for k, v in result.items()} == {
        (conversation.id, alice.id): sorted([a1.id, a2.id])
    }
    assert msgs.mark_delivered_for_user(bob.id) == {}
    db.session.expire_all()
    assert c1.delivered_at is None  # carol's message wasn't for bob


# ----------------------------------------------------------- conversation list

def test_list_for_user_orders_by_activity_and_counts_unread(db, make_user, duo):
    alice, bob, with_bob = duo
    carol = make_user("carol")
    with_carol, _ = convs.get_or_create_private(alice, carol)
    post(with_bob, bob, "b1")
    post(with_bob, bob, "b2")
    post(with_carol, carol, "c1")
    post(with_bob, alice, "my reply")  # most recent activity

    rows = convs.list_for_user(alice.id)
    assert [r["peer"].username for r in rows] == ["bob", "carol"]
    assert rows[0]["last_message"].body == "my reply"
    assert (rows[0]["unread"], rows[1]["unread"]) == (2, 1)

    msgs.mark_read(with_bob.id, alice.id)
    assert convs.list_for_user(alice.id)[0]["unread"] == 0


def test_list_for_user_includes_empty_conversations_and_only_mine(db, make_user, duo):
    alice, bob, _ = duo
    carol = make_user("carol")
    convs.get_or_create_private(bob, carol)
    rows = convs.list_for_user(alice.id)
    assert len(rows) == 1 and rows[0]["last_message"] is None and rows[0]["unread"] == 0
    assert convs.list_for_user(make_user("dave").id) == []


def test_list_for_user_uses_a_constant_number_of_queries(db, make_user):
    me = make_user("me")
    for i in range(12):
        other = make_user(f"friend{i}")
        conversation, _ = convs.get_or_create_private(me, other)
        post(conversation, other, "hi")
    db.session.expire_all()

    statements = []
    listener = lambda conn, cursor, statement, *a: statements.append(statement)  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        convs.list_for_user(me.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert len(statements) <= 4, statements


# ----------------------------------------------------------------- JSON API

def api(app, user):
    client = app.test_client()
    sign_in(client, user)
    return client


def test_api_requires_login_and_answers_json(app, db):
    client = app.test_client()
    for path in ("/api/conversations", "/api/conversations/1/messages", "/api/users/search?q=ab"):
        response = client.get(path)
        assert response.status_code == 401 and response.is_json, path


def test_api_conversation_list_shape_and_privacy(app, db, make_user, duo):
    alice, bob, conversation = duo
    post(conversation, bob, "ping")
    data = api(app, alice).get("/api/conversations").get_json()["conversations"]
    assert len(data) == 1
    item = data[0]
    assert item["unread"] == 1 and item["last_message"]["text"] == "ping"
    assert item["peer"]["username"] == "bob" and item["url"] == f"/chats/{conversation.id}"
    assert "email" not in str(item) and "password" not in str(item)
    # bob's list shows alice, not bob
    assert api(app, bob).get("/api/conversations").get_json()["conversations"][0]["peer"]["username"] == "alice"


def test_api_history_is_participants_only(app, db, make_user, duo):
    alice, bob, conversation = duo
    post(conversation, alice, "private words")
    carol = make_user("carol")

    ok = api(app, bob).get(f"/api/conversations/{conversation.id}/messages")
    assert ok.status_code == 200 and ok.get_json()["messages"][0]["body"] == "private words"

    stolen = api(app, carol).get(f"/api/conversations/{conversation.id}/messages")
    missing = api(app, carol).get("/api/conversations/999999/messages")
    assert stolen.status_code == missing.status_code == 404
    assert stolen.get_json() == missing.get_json()
    assert b"private words" not in stolen.data


def test_api_history_pagination_parameters(app, db, duo):
    alice, bob, conversation = duo
    for i in range(30):
        post(conversation, alice, f"m{i}")
    client = api(app, bob)
    page = client.get(f"/api/conversations/{conversation.id}/messages?limit=10").get_json()
    assert len(page["messages"]) == 10 and page["has_more"]
    older = client.get(
        f"/api/conversations/{conversation.id}/messages?limit=10&before={page['messages'][0]['id']}"
    ).get_json()
    assert older["messages"][-1]["body"] == "m19"
    garbage = client.get(f"/api/conversations/{conversation.id}/messages?before=abc&limit=x")
    assert garbage.status_code == 200


# -------------------------------------------------------------------- search

@pytest.fixture()
def people(make_user, db):
    names = {
        "alice": "Alice Smith", "alina": "Alina Petrova", "bob_100": "Bob Hundred",
        "bobx100": "Bobx Other", "ሰላም": None,
    }
    users = {}
    for username, display in names.items():
        if display is None:
            continue
        user = make_user(username)
        user.display_name = display
        users[username] = user
    db.session.commit()
    return users


def search(app, user, q):
    return [u["username"] for u in api(app, user).get(f"/api/users/search?q={q}").get_json()["users"]]


def test_search_prefix_substring_and_ordering(app, people):
    me = people["alice"]
    assert search(app, me, "ali") == ["alina"]  # never returns yourself
    assert search(app, me, "petrova") == ["alina"]  # display-name substring
    assert search(app, me, "ALINA") == ["alina"]  # case-insensitive
    assert search(app, me, "%40alina") == ["alina"]  # leading @ ignored


def test_search_requires_two_characters_and_trims_long_input(app, people):
    me = people["alice"]
    assert search(app, me, "a") == []
    assert search(app, me, "") == []
    assert search(app, me, "%20%20") == []
    assert search(app, me, "bo" + "z" * 200) == []


def test_search_treats_wildcards_literally(app, people):
    me = people["alice"]
    assert search(app, me, "%25") == []          # '%' must not match everything
    assert search(app, me, "_") == []            # too short anyway
    assert search(app, me, "bob_1") == ["bob_100"]  # '_' is literal, so bobx100 is not matched
    assert search(app, me, "bob%25") == []


def test_search_hides_inactive_users_and_private_fields(app, db, people):
    people["alina"].is_active = False
    db.session.commit()
    assert search(app, people["alice"], "ali") == []
    result = api(app, people["alice"]).get("/api/users/search?q=bob").get_json()["users"][0]
    assert set(result) == {"id", "username", "display_name", "avatar_url", "profile_url"}


def test_search_is_limited_to_ten_results(app, db, make_user):
    me = make_user("zed")
    for i in range(15):
        make_user(f"sam{i:02d}")
    assert len(search(app, me, "sam")) == 10


def test_search_is_rate_limited_per_user(app, people, rate_limits):
    client = api(app, people["alice"])
    for _ in range(60):
        assert client.get("/api/users/search?q=bob").status_code == 200
    assert client.get("/api/users/search?q=bob").status_code == 429
    assert client.get("/api/users/search?q=bob").is_json  # JSON, not an HTML page
    other = api(app, people["bob_100"])
    assert other.get("/api/users/search?q=ali").status_code == 200  # separate budget
