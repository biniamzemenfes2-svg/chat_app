import pytest

from app.models import Conversation, ConversationMember
from app.services import conversation_service as svc
from tests.conftest import sign_in


@pytest.fixture()
def trio(make_user):
    return make_user("alice"), make_user("bob"), make_user("carol")


# ------------------------------------------------------------------ service

def test_get_or_create_private_creates_once_and_is_symmetric(db, trio):
    alice, bob, _ = trio
    first, created = svc.get_or_create_private(alice, bob)
    again, created_again = svc.get_or_create_private(bob, alice)
    assert created and not created_again
    assert first.id == again.id
    assert db.session.query(Conversation).count() == 1
    assert sorted(m.user_id for m in first.members) == sorted([alice.id, bob.id])


def test_cannot_converse_with_yourself_or_inactive_users(db, trio):
    alice, bob, _ = trio
    with pytest.raises(svc.ConversationError):
        svc.get_or_create_private(alice, alice)
    bob.is_active = False
    db.session.commit()
    with pytest.raises(svc.ConversationError):
        svc.get_or_create_private(alice, bob)


def test_simultaneous_creation_resolves_to_one_conversation(db, trio, monkeypatch):
    """Both users click "Message" at once: the loser must get the winner's chat."""
    alice, bob, _ = trio
    winner, _ = svc.get_or_create_private(bob, alice)

    real_scalar = db.session.scalar
    calls = {"n": 0}

    def blind_first_lookup(statement, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "scalar", blind_first_lookup)
    conversation, created = svc.get_or_create_private(alice, bob)
    assert conversation.id == winner.id and not created
    assert db.session.query(Conversation).count() == 1


def test_membership_lookup_hides_foreign_conversations(db, trio):
    alice, bob, carol = trio
    conversation, _ = svc.get_or_create_private(alice, bob)
    assert svc.get_member_conversation(conversation.id, alice.id).id == conversation.id
    assert svc.get_member_conversation(conversation.id, bob.id).id == conversation.id
    assert svc.get_member_conversation(conversation.id, carol.id) is None
    assert svc.get_member_conversation(999999, alice.id) is None


def test_peer_helpers(db, trio):
    alice, bob, carol = trio
    svc.get_or_create_private(alice, bob)
    svc.get_or_create_private(alice, carol)
    conversation, _ = svc.get_or_create_private(bob, carol)
    assert svc.peer_of(conversation.id, bob.id).id == carol.id
    assert svc.peer_ids(alice.id) == {bob.id, carol.id}
    assert svc.other_member_ids(conversation.id, bob.id) == [carol.id]


# --------------------------------------------------------------------- HTTP

def test_start_conversation_via_profile_button(client, trio, db):
    alice, bob, _ = trio
    sign_in(client, alice)
    response = client.post("/conversations/start/bob")
    conversation = db.session.query(Conversation).one()
    assert response.status_code == 302
    assert response.headers["Location"] == f"/chats/{conversation.id}"
    # Pressing it again goes to the same conversation.
    again = client.post("/conversations/start/BOB")
    assert again.headers["Location"] == response.headers["Location"]
    assert db.session.query(Conversation).count() == 1


def test_start_conversation_edge_cases(client, trio):
    alice, bob, _ = trio
    sign_in(client, alice)
    assert client.post("/conversations/start/nobody").status_code == 404
    own = client.post("/conversations/start/alice")
    assert own.status_code == 302 and own.headers["Location"] == "/u/alice"
    assert client.get("/conversations/start/bob").status_code == 405


def test_start_conversation_requires_login(client, trio):
    assert client.post("/conversations/start/bob").status_code == 302


def test_profile_shows_message_button_only_for_others(client, trio):
    alice, bob, _ = trio
    sign_in(client, alice)
    assert b"/conversations/start/bob" in client.get("/u/bob").data
    assert b"/conversations/start/alice" not in client.get("/u/alice").data


def test_conversation_page_is_only_for_participants(app, trio, db):
    alice, bob, carol = trio
    conversation, _ = svc.get_or_create_private(alice, bob)
    url = f"/chats/{conversation.id}"

    for user, expected in ((alice, 200), (bob, 200), (carol, 404)):
        client = app.test_client()
        sign_in(client, user)
        assert client.get(url).status_code == expected, user.username
    assert app.test_client().get(url).status_code == 302  # anonymous
    carol_client = app.test_client()
    sign_in(carol_client, carol)
    assert carol_client.get("/chats/999999").status_code == 404  # same answer as foreign


def test_foreign_conversation_and_missing_one_look_identical(app, trio, db):
    alice, bob, carol = trio
    conversation, _ = svc.get_or_create_private(alice, bob)
    client = app.test_client()
    sign_in(client, carol)
    foreign = client.get(f"/chats/{conversation.id}")
    missing = client.get("/chats/424242")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.data == missing.data


def test_page_has_no_inline_script_for_message_content(app, trio, db):
    alice, bob, _ = trio
    conversation, _ = svc.get_or_create_private(alice, bob)
    client = app.test_client()
    sign_in(client, alice)
    html = client.get(f"/chats/{conversation.id}").get_data(as_text=True)
    import re
    assert " style=" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert 'data-peer-id="%d"' % bob.id in html
