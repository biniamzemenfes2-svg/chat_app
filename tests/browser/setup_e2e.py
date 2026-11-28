import json, secrets, sys
from app import create_app
from app.extensions import db
from datetime import datetime, timedelta, timezone

from app.models import Message, User
from app.services import conversation_service

app = create_app()
suffix = secrets.token_hex(3)
with app.app_context():
    users = {}
    for key, display in (("alice", "Alice Wonder"), ("bob", "Bob Builder"), ("carol", "Carol Danvers")):
        user = User(username=f"{key}_{suffix}", email=f"{key}_{suffix}@example.com", display_name=display)
        user.set_password("correct-horse-battery")
        db.session.add(user)
        users[key] = user
    db.session.commit()
    conversation, _ = conversation_service.get_or_create_private(users["alice"], users["bob"])

    # A long conversation (130 messages over two days) for paging, jumping and search.
    long_chat, _ = conversation_service.get_or_create_private(users["alice"], users["carol"])
    now = datetime.now(timezone.utc)
    for i in range(1, 131):
        when = now - timedelta(days=2) + timedelta(minutes=i) if i <= 60 else now - timedelta(minutes=131 - i)
        db.session.add(Message(
            conversation_id=long_chat.id,
            sender_id=users["alice"].id if i % 2 == 0 else users["carol"].id,
            body=f"history {i:03d}", message_type="text", created_at=when,
        ))
    db.session.commit()
    serializer = app.session_interface.get_signing_serializer(app)
    out = {"conversation_id": conversation.id, "conversation2_id": long_chat.id, "suffix": suffix}
    for key, user in users.items():
        out[key] = {
            "id": user.id, "username": user.username, "display_name": user.display_name,
            "cookie": serializer.dumps({"_user_id": user.get_id(), "_fresh": True}),
        }
    print(json.dumps(out))
