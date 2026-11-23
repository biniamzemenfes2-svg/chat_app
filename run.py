"""Development entry point.

    python run.py

Production uses a WSGI server instead; see README.md.
"""
import os

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    is_production = os.environ.get("FLASK_CONFIG") == "production"
    socketio.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=app.debug,
        allow_unsafe_werkzeug=not is_production,
    )
