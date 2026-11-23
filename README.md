# Chat

A private one-to-one messaging application (text, attachments, gifts, audio and
video calls) built with Flask, Flask-SocketIO, PostgreSQL and WebRTC.

> **Status: Phase 1 of 8 (foundation).** The application factory, configuration,
> database models and migrations are in place and tested. Accounts, messaging,
> the new interface and calling are built in the following phases. The original
> room-based prototype (`main.py`, `templates/`, `static/`) is still in the
> repository and is removed in Phase 3 when private messaging replaces it.

## Requirements

- Python 3.10+
- PostgreSQL 14+

## Setup

```bash
python3 -m venv ~/.venvs/chat          # keep the virtualenv outside the project
source ~/.venvs/chat/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env                 # then edit .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste as SECRET_KEY
```

### Database

Create a database and user (or use Docker):

```bash
# Option A: Docker
docker run -d --name chat-db -e POSTGRES_USER=chatapp -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=chatapp -p 5432:5432 postgres:16
# a second, throwaway database for the tests:
docker exec chat-db createdb -U chatapp chatapp_test

# Option B: local PostgreSQL
createuser --pwprompt chatapp
createdb -O chatapp chatapp
createdb -O chatapp chatapp_test
```

Set `DATABASE_URL` and `TEST_DATABASE_URL` in `.env`, then apply the schema:

```bash
flask --app run.py db upgrade
```

### Run

```bash
python run.py        # http://127.0.0.1:5000  (health check: /healthz)
```

### Tests

```bash
pytest
```

The tests **drop and recreate all tables** in `TEST_DATABASE_URL`. Never point it
at a database you care about.

## Migrations

The schema is managed by Alembic through Flask-Migrate. `db.create_all()` is only
used by the test suite.

```bash
flask --app run.py db migrate -m "describe the change"   # generate
# review the generated file in migrations/versions/ before applying it
flask --app run.py db upgrade
flask --app run.py db check                               # fails if models and migrations drifted
```

## Production

```bash
export FLASK_CONFIG=production SOCKETIO_ASYNC_MODE=gevent
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 127.0.0.1:8000 run:app
```

- Use exactly **one worker** until a Socket.IO message queue (Redis) is configured;
  Socket.IO rooms live in each process's memory.
- Put the app behind an HTTPS reverse proxy (nginx, Caddy) that forwards WebSocket
  upgrades, and set `PROXY_COUNT` to the number of proxies so rate limiting sees
  real client IPs.
- Production refuses to start without a real `SECRET_KEY`, and enables secure cookies.
- Browsers only allow camera and microphone on HTTPS, so calls require it.
- With the gevent worker, PostgreSQL calls through `psycopg2` block the worker unless
  made cooperative (e.g. with `psycogreen`). This is reviewed in Phase 8 before deploy.

## Project layout

```
app/
  __init__.py        application factory (create_app)
  extensions.py      db, migrate, login, csrf, limiter, socketio
  models/            SQLAlchemy models (one module per domain)
  routes/            Flask blueprints
  sockets/           Socket.IO handlers
  services/          business logic shared by routes and sockets
  templates/ static/
migrations/          Alembic migrations
tests/
config.py            configuration classes and validation
run.py               development entry point / WSGI target
```

## Architecture decisions

- **One private conversation per pair is a database guarantee.** `conversations.direct_key`
  holds `"<lower id>:<higher id>"` for private chats and is `UNIQUE`, so two
  simultaneous "Message" clicks cannot create duplicates.
- **Usernames and emails are stored lowercase**, enforced by a `CHECK`, so uniqueness
  is case-insensitive without functional indexes.
- **Deleting a user is restricted while they have messages, gifts or calls**; deleting
  a conversation cascades to its messages, attachments and calls at the database level.
- **Soft-deleted messages** keep their row (`deleted_at`) so reply references and
  read receipts stay consistent.
- **Socket.IO connections require an authenticated session.** Each user joins a room
  named `user:<id>` derived from the server-side session, never from client input.
- **No end-to-end encryption is claimed.** Access control is enforced server-side.
- **Schema additions beyond the original spec:** `message_reactions`, `messages.reply_to_id`,
  `messages.delivered_at`, `sent_gifts.message_id`, `calls.answered_at`.

## Known limitations

- Calls need a TURN server to work reliably across mobile networks and strict NATs.
  It is configured in Phase 5; none is provided by this project.
- `migrations/env.py` is the stock Flask-Migrate template and emits a deprecation
  warning about `get_engine` under Flask-SQLAlchemy 3.1. It is harmless today.
