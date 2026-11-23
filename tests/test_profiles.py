import io

import pytest
from PIL import Image

from app.services import avatar_service
from tests.conftest import sign_in

PASSWORD = "correct-horse-battery"


def image_bytes(fmt="PNG", size=(400, 300), color=(200, 30, 30), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(data, name="me.png"):
    return (io.BytesIO(data), name)


def save_profile(client, **fields):
    data = {"display_name": "Alice", "bio": ""}
    data.update(fields)
    return client.post("/settings/profile", data=data, content_type="multipart/form-data")


@pytest.fixture()
def alice(client, make_user):
    user = make_user("alice", password=PASSWORD)
    sign_in(client, user)
    return user


# ------------------------------------------------------------------ profile view

def test_profile_page_shows_public_details(client, alice, make_user):
    bob = make_user("bob")
    bob.bio = "Hello from Bob"
    from app.extensions import db
    db.session.commit()
    response = client.get("/u/bob")
    assert response.status_code == 200
    assert b"@bob" in response.data and b"Hello from Bob" in response.data
    assert b"Member since" in response.data
    assert b"bob@example.com" not in response.data  # email is private


def test_profile_lookup_is_case_insensitive_and_404s_for_unknown(client, alice):
    assert client.get("/u/ALICE").status_code == 200
    assert client.get("/u/nobody").status_code == 404


def test_inactive_user_profile_is_hidden(client, alice, make_user, db):
    bob = make_user("bob")
    bob.is_active = False
    db.session.commit()
    assert client.get("/u/bob").status_code == 404


def test_profile_pages_require_login(client, make_user):
    make_user("bob")
    assert client.get("/u/bob").status_code == 302
    assert client.get("/settings/profile").status_code == 302
    assert client.get("/settings/password").status_code == 302


def test_user_content_is_html_escaped(client, alice, db):
    response = save_profile(
        client, display_name="<script>alert(1)</script>", bio='<img src=x onerror="alert(2)">'
    )
    assert response.status_code == 302
    page = client.get("/u/alice").get_data(as_text=True)
    assert "<script>alert(1)" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page


# ------------------------------------------------------------------ profile edit

def test_edit_display_name_and_bio(client, alice, db):
    response = save_profile(client, display_name="  Alice Wonder ", bio="  Curious.  ")
    assert response.status_code == 302
    db.session.refresh(alice)
    assert (alice.display_name, alice.bio) == ("Alice Wonder", "Curious.")


@pytest.mark.parametrize(
    "fields",
    [{"display_name": ""}, {"display_name": "x" * 65}, {"bio": "x" * 281}, {"display_name": "a\x00b"}],
)
def test_edit_validates_input(client, alice, db, fields):
    assert save_profile(client, **fields).status_code == 400
    db.session.refresh(alice)
    assert alice.display_name == "Alice"


def test_username_cannot_be_changed_through_the_form(client, alice, db):
    save_profile(client, username="mallory", email="m@evil.example")
    db.session.refresh(alice)
    assert (alice.username, alice.email) == ("alice", "alice@example.com")


# ---------------------------------------------------------------------- avatars

def test_valid_upload_is_reencoded_square_webp(client, alice, db, upload_folder):
    response = save_profile(client, avatar=upload(image_bytes("PNG", (400, 300))))
    assert response.status_code == 302
    db.session.refresh(alice)

    assert avatar_service.FILENAME_RE.match(alice.avatar)
    stored = upload_folder / "avatars" / alice.avatar
    with Image.open(stored) as image:
        assert image.format == "WEBP"
        assert image.size == (256, 256)


def test_upload_strips_exif_metadata(client, alice, db, upload_folder):
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "blue").save(buffer, format="JPEG", exif=exif)
    assert "SecretCameraMaker" in buffer.getvalue().decode("latin-1")

    save_profile(client, avatar=upload(buffer.getvalue(), "photo.jpg"))
    db.session.refresh(alice)
    stored = (upload_folder / "avatars" / alice.avatar).read_bytes()
    assert b"SecretCameraMaker" not in stored


def test_replacing_avatar_deletes_the_old_file(client, alice, db, upload_folder):
    save_profile(client, avatar=upload(image_bytes()))
    db.session.refresh(alice)
    first = alice.avatar
    save_profile(client, avatar=upload(image_bytes(color=(0, 0, 255))))
    db.session.refresh(alice)
    assert alice.avatar != first
    assert not (upload_folder / "avatars" / first).exists()
    assert len(list((upload_folder / "avatars").iterdir())) == 1


def test_remove_avatar(client, alice, db, upload_folder):
    save_profile(client, avatar=upload(image_bytes()))
    db.session.refresh(alice)
    name = alice.avatar
    save_profile(client, remove_avatar="y")
    db.session.refresh(alice)
    assert alice.avatar is None
    assert not (upload_folder / "avatars" / name).exists()


def test_saving_without_a_file_keeps_the_avatar(client, alice, db):
    save_profile(client, avatar=upload(image_bytes()))
    db.session.refresh(alice)
    name = alice.avatar
    save_profile(client, display_name="Alice B")
    db.session.refresh(alice)
    assert alice.avatar == name


@pytest.mark.parametrize(
    "data, filename, fragment",
    [
        (b"just some text pretending to be a picture", "x.png", b"not a valid image"),
        (b"", "empty.png", b"empty"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "x.svg", b"not a valid image"),
        (b"GIF89a" + b"\x00" * 50, "x.gif", b"not a valid image"),
    ],
)
def test_rejects_non_images(client, alice, db, upload_folder, data, filename, fragment):
    response = save_profile(client, avatar=upload(data, filename))
    assert response.status_code == 400
    assert fragment in response.data
    db.session.refresh(alice)
    assert alice.avatar is None
    assert not (upload_folder / "avatars").exists()


def test_rejects_unsupported_image_format(client, alice):
    gif = image_bytes("GIF")
    response = save_profile(client, avatar=upload(gif, "x.gif"))
    assert response.status_code == 400
    assert b"JPEG, PNG or WebP" in response.data


def test_rejects_oversized_file(app, client, alice):
    app.config["AVATAR_MAX_BYTES"] = 1024
    try:
        response = save_profile(client, avatar=upload(image_bytes(size=(500, 500))))
    finally:
        app.config["AVATAR_MAX_BYTES"] = 2 * 1024 * 1024
    assert response.status_code == 400
    assert b"too large" in response.data


def test_rejects_excessive_dimensions_before_decoding(client, alice, monkeypatch):
    monkeypatch.setattr(avatar_service, "MAX_PIXELS", 10_000)
    response = save_profile(client, avatar=upload(image_bytes(size=(200, 200))))
    assert response.status_code == 400
    assert b"dimensions" in response.data


def test_rejects_tiny_images(client, alice):
    assert save_profile(client, avatar=upload(image_bytes(size=(8, 8)))).status_code == 400


def test_transparent_png_is_supported(client, alice, db, upload_folder):
    data = image_bytes("PNG", (200, 200), (255, 0, 0, 0), "RGBA")
    assert save_profile(client, avatar=upload(data)).status_code == 302


# ---------------------------------------------------------------- avatar serving

def test_avatar_is_served_to_signed_in_users_only(client, alice, db, make_user):
    save_profile(client, avatar=upload(image_bytes()))
    db.session.refresh(alice)
    url = f"/avatars/{alice.avatar}"

    response = client.get(url)
    assert response.status_code == 200
    assert response.mimetype == "image/webp"

    anonymous = client.application.test_client()
    assert anonymous.get(url).status_code == 302


@pytest.mark.parametrize(
    "name",
    ["../config.py", "..%2Fconfig.py", "nonexistent.webp", "A" * 32 + ".webp", "x" * 32 + ".png", ".hidden.tmp"],
)
def test_avatar_route_rejects_bad_names(client, alice, name):
    assert client.get(f"/avatars/{name}").status_code == 404


def test_profile_page_links_the_avatar_image(client, alice, db):
    save_profile(client, avatar=upload(image_bytes()))
    db.session.refresh(alice)
    assert f"/avatars/{alice.avatar}".encode() in client.get("/u/alice").data


# --------------------------------------------------------------- password change

def change_password(client, current=PASSWORD, new="a-brand-new-passphrase", confirm=None):
    return client.post(
        "/settings/password",
        data={
            "current_password": current,
            "new_password": new,
            "confirm_new_password": confirm if confirm is not None else new,
        },
    )


def test_change_password_success(client, alice, db):
    assert change_password(client).status_code == 302
    db.session.refresh(alice)
    assert alice.check_password("a-brand-new-passphrase")
    assert not alice.check_password(PASSWORD)
    assert client.get("/settings/profile").status_code == 200  # this browser stays signed in


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"current": "wrong-current-password"}, b"incorrect"),
        ({"new": "short"}, b"10-128"),
        ({"new": PASSWORD}, b"different from your current"),
        ({"new": "a-brand-new-passphrase", "confirm": "something-else-here"}, b"do not match"),
        ({"new": "my-alice-secret-pw"}, b"contain your username"),
    ],
)
def test_change_password_validation(client, alice, db, kwargs, fragment):
    response = change_password(client, **kwargs)
    assert response.status_code == 400
    assert fragment in response.data
    db.session.refresh(alice)
    assert alice.check_password(PASSWORD)


def test_password_change_signs_out_other_devices(app, client, alice):
    other_device = app.test_client()
    sign_in(other_device, alice)
    assert other_device.get("/settings/profile").status_code == 200

    assert change_password(client).status_code == 302

    assert other_device.get("/settings/profile").status_code == 302  # signed out
    assert client.get("/settings/profile").status_code == 200  # this one is not


def test_password_change_invalidates_other_remember_cookies(app, client, make_user):
    user = make_user("alice", password=PASSWORD)
    phone = app.test_client()
    phone.post("/login", data={"identifier": "alice", "password": PASSWORD, "remember": "y"})
    assert phone.get_cookie("remember_token") is not None
    phone.delete_cookie("session")  # simulate the browser session ending

    sign_in(client, user)
    change_password(client)

    assert phone.get("/settings/profile").status_code == 302


def test_new_password_works_for_login_and_old_does_not(app, client, alice):
    change_password(client)
    ok = app.test_client().post(
        "/login", data={"identifier": "alice", "password": "a-brand-new-passphrase"}
    )
    bad = app.test_client().post(
        "/login", data={"identifier": "alice", "password": PASSWORD}
    )
    assert (ok.status_code, bad.status_code) == (302, 401)
