# Chat

A private one-to-one messaging application (text, attachments, gifts, audio and
video calls) built with Flask, Flask-SocketIO, PostgreSQL and WebRTC.

> **Status: Phase 3 of 8 (private messaging).** Accounts, profiles and real-time
> one-to-one messaging work end to end. The interface is functional but plain; the
> professional redesign, emoji/reactions, calls, gifts and attachments come in later
> phases. The original room-based prototype has been removed.

## What works today

- **Registration** with username, email, display name and password (confirmation,
  strength rules, duplicate prevention for usernames and emails, case-insensitive).
- **Sign in** with username *or* email, optional "keep me signed in", sign out.
- **Profiles** (`/u/<username>`) with picture, bio and join date; **edit profile**
  and **change password** under `/settings`.
- **Profile pictures**: validated, cropped to a square and re-encoded as WebP
  (metadata is stripped); served only to signed-in users.
- **Security**: scrypt password hashing, CSRF on every form, per-IP and per-account
  limits on failed logins, rate-limited registration, open-redirect protection,
  strict Content-Security-Policy, secure cookie flags, generic login errors.
- Changing your password **signs out every other device**, including "remember me".
- **Find people** by username or name, open their profile and press **Message**.
- **Private one-to-one conversations**, one per pair of users (guaranteed by the database).
  Only the two participants can read them, over HTTP or Socket.IO.
- **Real-time messaging** with persistent history (survives logout and server restarts),
  paging of older messages, and idempotent sending (a retry never duplicates a message).
- **Sent → Delivered → Read** receipts, unread counts, typing indicators, and
  online / last-seen presence (shared only with people you have a conversation with).

### Not built yet

Message edit, delete, reply, copy and in-conversation search; emoji picker and reactions;
attachments; gifts; audio/video calls; the final responsive design; notifications outside
an open page. Each is planned in the remaining phases.

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
# then open /register to create an account
```

### Tests

```bash
pytest                      # unit, API and Socket.IO tests (226 tests)
tests/browser/run.sh        # optional: real page JavaScript in two simulated browsers
```

The tests **drop and recreate all tables** in `TEST_DATABASE_URL`. Never point it
at a database you care about.

`tests/browser/run.sh` starts the real server and drives `conversation.js`,
`dashboard.js` and `realtime.js` in two jsdom "browsers" that chat with each other
(live delivery, receipts, typing, presence, XSS payloads, lost-connection retry). It
needs Node 18+. jsdom is not a real browser: it does not enforce the
Content-Security-Policy or do layout, so look at the pages in a real browser too.

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
- Presence and the real-time rate limits live in process memory, which is another reason
  for a single worker. Scaling out needs a Socket.IO message queue and a shared presence
  store (Redis); this is not implemented.
- With the gevent worker, PostgreSQL calls through `psycopg2` block the worker unless
  made cooperative (e.g. with `psycogreen`). This is reviewed in Phase 8 before deploy.

## Project layout

```
app/
  __init__.py        application factory (create_app)
  extensions.py      db, migrate, login, csrf, limiter, socketio
  models/            SQLAlchemy models (one module per domain)
  forms/             Flask-WTF forms and validators
  routes/            Flask blueprints (main, auth, profiles, settings, users, conversations)
  sockets/           Socket.IO handlers (presence, messaging) and shared helpers
  services/          business logic shared by routes and sockets
  utils/             small helpers (safe redirects)
  templates/ static/
migrations/          Alembic migrations
tests/               pytest suite; tests/browser holds the jsdom end-to-end test
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
- **Sessions carry a per-user token** (`users.session_token`, stored in the session as
  `<id>:<token>`). Rotating it on password change revokes all other sessions without a
  server-side session store.
- **No third-party assets.** Bootstrap 5.3.8 is vendored under `app/static/vendor/`, so
  the Content-Security-Policy allows only this origin: no inline scripts or styles, no CDNs.
  Templates must not use `style=` attributes or inline `<script>` (a test enforces this).
- **Only failed logins count toward the login limits**, per IP and per targeted account
  (`LOGIN_LIMIT_PER_IP`, `LOGIN_LIMIT_PER_ACCOUNT`). Trade-off: someone can deliberately
  lock a known account for the window by failing against it; the alternative is leaving
  distributed guessing unchecked.
- **Uploaded avatars are never stored as received**: decoded, size-checked (bytes and
  pixels), cropped, re-encoded, saved under a random name outside `static/`.
- **Real-time delivery uses one private room per user** (`user:<id>`, assigned from the
  session). There is no "join conversation" event, so a client cannot subscribe to anything
  it was not given; every event is addressed to participants looked up on the server.
- **Authorization is one function.** `get_member_conversation(conversation_id, user_id)`
  is the only way HTTP routes and socket handlers obtain a conversation. Non-participants get
  the same "not found" as for a conversation that does not exist, so ids reveal nothing.
- **Sockets are re-authenticated on every event**, not just at connect. A connection that
  outlives its login (password change, logout, deactivation) is refused and closed, and the
  server also closes a user's live sockets on logout and password change. Still-valid
  devices reconnect automatically.
- **Cross-site WebSocket hijacking is blocked** by Socket.IO's same-origin check
  (`SOCKETIO_CORS_ORIGINS` is empty by default).
- **Messages are stored as typed and rendered with `textContent`**; the server never
  stores HTML-escaped text and the client never uses `innerHTML` for user content.
- **Sending is idempotent.** The client attaches a random `client_id`; `UNIQUE(sender_id,
  client_id)` means a retry after a lost acknowledgement returns the original message.
- **Delivered means "reached the recipient's connection".** If the recipient is online
  when a message is sent it is delivered at once; otherwise at their next connect.
  Read means they had the conversation open and visible.
- **No end-to-end encryption is claimed.** Access control is enforced server-side.
- **Schema additions beyond the original spec:** `message_reactions`, `messages.reply_to_id`,
  `messages.delivered_at`, `messages.client_id`, `sent_gifts.message_id`, `calls.answered_at`, `users.session_token`.

## Known limitations

- There is no email verification or "forgot password" flow yet (not in the current scope).
  A user who forgets their password cannot recover the account.
- The common-password check is a very small list; it catches only the laziest choices.
- The interface has been verified through automated tests and HTTP-level checks, but
  not yet in real browsers; layout and the Content-Security-Policy deserve a manual look
  (Phase 4 covers the full UI and responsive testing).
- User search scans display names with `ILIKE`; fine for thousands of users, but a
  trigram index (`pg_trgm`) is the next step beyond that.
- Presence reveals when someone was last online to everyone they have a conversation
  with; there is no setting to hide it yet.
- Rate limits use in-memory storage by default; behind several workers set
  `RATELIMIT_STORAGE_URI` to Redis or each worker counts separately.
- Calls need a TURN server to work reliably across mobile networks and strict NATs.
  It is configured in Phase 5; none is provided by this project.
- `migrations/env.py` is the stock Flask-Migrate template and emits a deprecation
  warning about `get_engine` under Flask-SQLAlchemy 3.1. It is harmless today.
