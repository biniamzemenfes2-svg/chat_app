import pytest

from app.models import User
from tests.conftest import sign_in

PASSWORD = "correct-horse-battery"


def register(client, **overrides):
    data = {
        "display_name": "Alice Example",
        "username": "alice",
        "email": "alice@example.com",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    data.update(overrides)
    return client.post("/register", data=data)


def login(client, identifier="alice", password=PASSWORD, **extra):
    return client.post(
        "/login", data={"identifier": identifier, "password": password, **extra}
    )


# ---------------------------------------------------------------- registration

def test_register_creates_user_hashes_password_and_signs_in(client, db):
    response = register(client)
    assert response.status_code == 302

    user = db.session.query(User).one()
    assert user.username == "alice"
    assert user.password_hash != PASSWORD and PASSWORD not in user.password_hash
    assert user.check_password(PASSWORD)
    assert client.get("/settings/profile").status_code == 200  # signed in


def test_register_normalises_username_and_email_case(client, db):
    register(client, username="  AliCe ", email=" Alice@Example.COM ")
    user = db.session.query(User).one()
    assert (user.username, user.email) == ("alice", "alice@example.com")


def test_register_rejects_duplicate_username_case_insensitively(client, make_user):
    make_user("alice")
    response = register(client, username="ALICE", email="other@example.com")
    assert response.status_code == 400
    assert b"already taken" in response.data


def test_register_rejects_duplicate_email(client, make_user):
    make_user("bob", email="alice@example.com")
    response = register(client, username="alice")
    assert response.status_code == 400
    assert b"already exists" in response.data


@pytest.mark.parametrize(
    "password, fragment",
    [
        ("short1", b"10-128"),
        ("1234567890123", b"only numbers"),
        ("password123", b"too common"),
        ("my-alice-secret-pw", b"contain your username"),
    ],
)
def test_register_rejects_weak_passwords(client, db, password, fragment):
    response = register(client, password=password, confirm_password=password)
    assert response.status_code == 400
    assert fragment in response.data
    assert db.session.query(User).count() == 0


def test_register_rejects_mismatched_confirmation(client, db):
    response = register(client, confirm_password="something-else-entirely")
    assert response.status_code == 400
    assert b"do not match" in response.data


@pytest.mark.parametrize("username", ["ab", "has space", "bad-char!", "admin", "a" * 33])
def test_register_rejects_bad_or_reserved_usernames(client, db, username):
    assert register(client, username=username).status_code == 400
    assert db.session.query(User).count() == 0


def test_register_rejects_invalid_email(client, db):
    assert register(client, email="not-an-email").status_code == 400


def test_register_rejects_control_characters_in_display_name(client, db):
    assert register(client, display_name="Eve\x00\nEvil").status_code == 400


def test_register_accepts_non_latin_display_name(client, db):
    assert register(client, display_name="አለም ጌትሽ").status_code == 302
    assert db.session.query(User).one().display_name == "አለም ጌትሽ"


def test_register_handles_race_on_unique_username(client, db, make_user, monkeypatch):
    """Validation passes but the row appears before the insert: no 500."""
    make_user("alice", email="first@example.com")
    monkeypatch.setattr("app.services.auth_service.username_taken", lambda u: False)
    response = register(client)
    assert response.status_code == 400
    assert db.session.query(User).count() == 1


# ----------------------------------------------------------------------- login

def test_login_with_username_and_with_email_case_insensitive(client, make_user):
    make_user("alice", password=PASSWORD)
    assert login(client, "ALICE").status_code == 302
    client.post("/logout")
    assert login(client, "Alice@Example.com").status_code == 302


def test_login_failure_is_generic_for_wrong_password_and_unknown_user(client, make_user):
    make_user("alice", password=PASSWORD)
    wrong_password = login(client, "alice", "not-the-password")
    unknown_user = login(client, "nobody", "not-the-password")
    assert wrong_password.status_code == unknown_user.status_code == 401
    for response in (wrong_password, unknown_user):
        assert b"Incorrect username/email or password." in response.data


def test_inactive_account_cannot_log_in(client, db, make_user):
    user = make_user("alice", password=PASSWORD)
    user.is_active = False
    db.session.commit()
    assert login(client).status_code == 401


def test_deactivated_user_loses_existing_session(client, db, make_user):
    user = make_user("alice")
    sign_in(client, user)
    assert client.get("/settings/profile").status_code == 200
    user.is_active = False
    db.session.commit()
    assert client.get("/settings/profile").status_code == 302


def test_remember_me_sets_cookie_only_when_requested(client, make_user):
    make_user("alice", password=PASSWORD)
    login(client, remember="y")
    assert client.get_cookie("remember_token") is not None

    client.post("/logout")
    client.delete_cookie("remember_token")
    login(client)
    assert client.get_cookie("remember_token") is None


def test_logout_requires_post_and_signs_out(client, make_user):
    user = make_user("alice")
    sign_in(client, user)
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert client.get("/settings/profile").status_code == 302


def test_login_rejects_garbage_session_value(client, make_user):
    make_user("alice")
    for bad in ("1", "x:y", "", "1:", ":tok", "999:tok"):
        with client.session_transaction() as session:
            session["_user_id"] = bad
        assert client.get("/settings/profile").status_code == 302


def test_stale_session_token_is_rejected(client, make_user):
    user = make_user("alice")
    with client.session_transaction() as session:
        session["_user_id"] = f"{user.id}:{'0' * 32}"
    assert client.get("/settings/profile").status_code == 302


def test_protected_page_redirects_to_login_with_next(client):
    response = client.get("/settings/profile")
    assert response.status_code == 302
    from urllib.parse import parse_qs, urlsplit

    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query) == {"next": ["/settings/profile"]}


@pytest.mark.parametrize(
    "next_url",
    ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1"],
)
def test_login_ignores_unsafe_next_urls(client, make_user, next_url):
    make_user("alice", password=PASSWORD)
    response = client.post(
        "/login", query_string={"next": next_url},
        data={"identifier": "alice", "password": PASSWORD},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_login_follows_safe_next_url(client, make_user):
    make_user("alice", password=PASSWORD)
    response = client.post(
        "/login", query_string={"next": "/settings/password"},
        data={"identifier": "alice", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/settings/password"


def test_signed_in_users_are_redirected_away_from_auth_pages(client, make_user):
    sign_in(client, make_user("alice"))
    assert client.get("/login").status_code == 302
    assert client.get("/register").status_code == 302


# ------------------------------------------------------------------ CSRF / limits

def test_csrf_token_is_required_when_enabled(app, client, make_user):
    make_user("alice", password=PASSWORD)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = login(client)
        assert response.status_code == 400
        assert client.post("/logout").status_code in (400, 302)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_logout_without_csrf_token_is_rejected(app, client, make_user):
    sign_in(client, make_user("alice"))
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post("/logout").status_code == 400
        assert client.get("/settings/profile").status_code == 200  # still signed in
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_failed_logins_are_limited_per_account(client, make_user, rate_limits):
    make_user("alice", password=PASSWORD)
    for _ in range(10):
        assert login(client, "alice", "wrong-password-1").status_code == 401
    assert login(client, "alice", "wrong-password-1").status_code == 429
    # Even the right password is refused while the account bucket is exhausted.
    assert login(client, "alice", PASSWORD).status_code == 429
    # A different account from the same IP is unaffected.
    make_user("bob", password=PASSWORD)
    assert login(client, "bob", PASSWORD).status_code == 302


def test_successful_logins_do_not_consume_the_limit(client, make_user, rate_limits):
    make_user("alice", password=PASSWORD)
    for _ in range(15):
        assert login(client).status_code == 302
        client.post("/logout")


def test_failed_logins_are_limited_per_ip(app, client, make_user, rate_limits):
    app.config["LOGIN_LIMIT_PER_ACCOUNT"] = "1000 per hour"
    try:
        for i in range(30):
            assert login(client, f"user{i}", "wrong-password-1").status_code == 401
        assert login(client, "user99", "wrong-password-1").status_code == 429
    finally:
        app.config["LOGIN_LIMIT_PER_ACCOUNT"] = "10 per 15 minutes"


def test_registration_is_rate_limited(client, db, rate_limits):
    for i in range(10):
        register(client, username=f"user{i}", email=f"user{i}@example.com")
        client.post("/logout")
    assert register(client, username="late", email="late@example.com").status_code == 429


# ------------------------------------------------------------------- page hygiene

@pytest.mark.parametrize("path", ["/", "/login", "/register"])
def test_public_pages_have_no_inline_script_or_style(client, path):
    html = client.get(path).get_data(as_text=True)
    assert " style=" not in html
    assert "<style" not in html
    import re
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "default-src 'self'" in client.get(path).headers["Content-Security-Policy"]


def test_error_pages_render(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert b"Page not found" in response.data
