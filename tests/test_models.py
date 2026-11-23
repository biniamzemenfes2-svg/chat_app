import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Call,
    Conversation,
    ConversationMember,
    Gift,
    Message,
    SentGift,
    User,
)


def _private_conversation(db, a, b):
    conversation = Conversation(
        conversation_type="private",
        direct_key=Conversation.make_direct_key(a.id, b.id),
    )
    conversation.members = [
        ConversationMember(user_id=a.id),
        ConversationMember(user_id=b.id),
    ]
    db.session.add(conversation)
    db.session.commit()
    return conversation


def test_password_is_hashed_and_verifiable(make_user):
    user = make_user("alice", password="s3cret-passphrase")
    assert "s3cret-passphrase" not in user.password_hash
    assert user.check_password("s3cret-passphrase")
    assert not user.check_password("wrong")


def test_username_must_be_lowercase(db):
    db.session.add(
        User(username="Alice", email="a@example.com", display_name="A", password_hash="x")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_duplicate_username_and_email_rejected(db, make_user):
    make_user("alice", email="alice@example.com")
    db.session.add(
        User(username="alice", email="other@example.com", display_name="A", password_hash="x")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    db.session.add(
        User(username="other", email="alice@example.com", display_name="A", password_hash="x")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_direct_key_is_order_independent():
    assert Conversation.make_direct_key(7, 3) == Conversation.make_direct_key(3, 7) == "3:7"


def test_only_one_private_conversation_per_pair(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    _private_conversation(db, alice, bob)
    duplicate = Conversation(
        conversation_type="private",
        direct_key=Conversation.make_direct_key(bob.id, alice.id),
    )
    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_private_conversation_requires_direct_key(db):
    db.session.add(Conversation(conversation_type="private", direct_key=None))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_user_cannot_join_conversation_twice(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    db.session.add(ConversationMember(conversation_id=conversation.id, user_id=alice.id))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_text_message_requires_body(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    db.session.add(
        Message(conversation_id=conversation.id, sender_id=alice.id, message_type="text")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_deleting_conversation_cascades_to_messages(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    db.session.add(Message(conversation_id=conversation.id, sender_id=alice.id, body="hi"))
    db.session.commit()

    db.session.delete(conversation)
    db.session.commit()
    assert db.session.query(Message).count() == 0
    assert db.session.query(ConversationMember).count() == 0


def test_user_with_messages_cannot_be_deleted(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    db.session.add(Message(conversation_id=conversation.id, sender_id=alice.id, body="hi"))
    db.session.commit()

    db.session.delete(alice)
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_cannot_gift_or_call_yourself(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    gift = Gift(name="Heart", emoji="❤️")
    db.session.add(gift)
    db.session.commit()

    db.session.add(
        SentGift(
            gift_id=gift.id, sender_id=alice.id, recipient_id=alice.id,
            conversation_id=conversation.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    db.session.add(
        Call(
            conversation_id=conversation.id, caller_id=alice.id,
            receiver_id=alice.id, call_type="audio",
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_call_type_and_status_are_validated(db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation = _private_conversation(db, alice, bob)
    db.session.add(
        Call(
            conversation_id=conversation.id, caller_id=alice.id,
            receiver_id=bob.id, call_type="hologram",
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
