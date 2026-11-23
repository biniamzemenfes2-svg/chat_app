from flask_login import current_user
from flask_socketio import join_room

from app.extensions import socketio


@socketio.on("connect")
def handle_connect(auth):
    """Only authenticated users may hold a Socket.IO connection.

    Each user joins a private room named after their own id; notifications and
    call signaling are delivered there. The id comes from the server-side
    session, never from anything the client sends.
    """
    if not current_user.is_authenticated:
        return False
    join_room(f"user:{current_user.id}")
