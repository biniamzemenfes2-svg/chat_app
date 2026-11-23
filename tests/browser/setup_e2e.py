import json, secrets, sys
from app import create_app
from app.extensions import db
from app.models import User
from app.services import conversation_service

app = create_app()
suffix = secrets.token_hex(3)
with app.app_context():
    users = {}
    for key, display in (("alice", "Alice Wonder"), ("bob", "Bob Builder")):
        user = User(username=f"{key}_{suffix}", email=f"{key}_{suffix}@example.com", display_name=display)
        user.set_password("correct-horse-battery")
        db.session.add(user)
        users[key] = user
    db.session.commit()
    conversation, _ = conversation_service.get_or_create_private(users["alice"], users["bob"])
    serializer = app.session_interface.get_signing_serializer(app)
    out = {"conversation_id": conversation.id, "suffix": suffix}
    for key, user in users.items():
        out[key] = {
            "id": user.id, "username": user.username, "display_name": user.display_name,
            "cookie": serializer.dumps({"_user_id": user.get_id(), "_fresh": True}),
        }
    print(json.dumps(out))
