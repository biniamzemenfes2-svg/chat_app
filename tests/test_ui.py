"""Structure checks for the rendered pages and their static assets."""
import json
import re
from pathlib import Path

import pytest

from app.services import conversation_service as convs
from tests.conftest import sign_in

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.fixture()
def signed_in(app, db, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    conversation, _ = convs.get_or_create_private(alice, bob)
    client = app.test_client()
    sign_in(client, alice)
    return client, alice, bob, conversation


def local_assets(html):
    return sorted(set(re.findall(r'(?:href|src)="(/static/[^"?#]+)', html)))


def test_dashboard_uses_the_two_pane_shell(signed_in):
    client, alice, _, _ = signed_in
    html = client.get("/").get_data(as_text=True)
    assert "app-view-list" in html and 'id="conversation-list"' in html
    assert 'id="user-search"' in html and "Suggested actions" in html
    assert "app-navbar" not in html  # the shell replaces the top navbar
    assert 'data-emoji-url="/static/data/emoji.json"' in html
    assert f'data-me-id="{alice.id}"' in html


def test_dashboard_suggests_profile_completion_only_when_needed(signed_in, db):
    client, alice, _, _ = signed_in
    assert "Complete your profile" in client.get("/").get_data(as_text=True)
    alice.bio = "hello"
    alice.avatar = "a" * 32 + ".webp"
    db.session.commit()
    assert "Complete your profile" not in client.get("/").get_data(as_text=True)


def test_conversation_page_has_all_real_controls(signed_in):
    client, alice, bob, conversation = signed_in
    html = client.get(f"/chats/{conversation.id}").get_data(as_text=True)
    assert "app-view-chat" in html
    assert f'data-active-conversation="{conversation.id}"' in html
    for element_id in ("composer-input", "emoji-button", "send-button", "chat-search-input",
                       "delete-modal", "context-bar", "jump-latest", "load-earlier", "load-newer"):
        assert f'id="{element_id}"' in html, element_id
    assert f"@{bob.username}" in html
    # Nothing that isn't built yet is offered.
    for absent in ("call", "attach", "gift", "video"):
        assert not re.search(rf'id="[^"]*{absent}[^"]*"', html, re.I), absent


def test_plain_pages_keep_the_navbar(signed_in):
    client, *_ = signed_in
    for path in ("/settings/profile", "/u/alice"):
        assert "app-navbar" in client.get(path).get_data(as_text=True), path


@pytest.mark.parametrize("path", ["/", "/login", "/register", "/settings/profile", "/settings/password"])
def test_every_referenced_static_asset_exists(signed_in, path):
    client, *_ = signed_in
    anon = client.application.test_client()
    html = (anon if path in ("/login", "/register") else client).get(path).get_data(as_text=True)
    assets = local_assets(html)
    assert assets
    for asset in assets:
        assert (STATIC / asset.removeprefix("/static/")).is_file(), f"{asset} on {path}"


def test_conversation_page_assets_exist(signed_in):
    client, _, _, conversation = signed_in
    html = client.get(f"/chats/{conversation.id}").get_data(as_text=True)
    scripts = [a for a in local_assets(html) if a.endswith(".js")]
    assert {"/static/js/conversation.js", "/static/js/sidebar.js", "/static/js/emoji-picker.js",
            "/static/js/realtime.js", "/static/js/format.js"} <= set(scripts)
    for asset in local_assets(html):
        assert (STATIC / asset.removeprefix("/static/")).is_file(), asset


def test_icon_font_files_referenced_by_the_css_exist():
    css = (STATIC / "vendor/bootstrap-icons/bootstrap-icons.min.css").read_text()
    urls = re.findall(r'url\("([^")?]+)', css)
    assert urls
    for url in urls:
        assert (STATIC / "vendor/bootstrap-icons" / url).is_file(), url


def test_stylesheet_has_the_responsive_breakpoints():
    css = (STATIC / "css/app.css").read_text()
    assert "@media (max-width: 991.98px)" in css and "@media (max-width: 575.98px)" in css
    assert ".app-view-chat .sidebar" in css and ".app-view-list .app-pane" in css
    assert "100dvh" in css and "prefers-reduced-motion" in css


def test_csp_allows_the_icon_font_and_emoji_catalog(signed_in):
    client, *_ = signed_in
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "font-src" not in csp  # fonts fall back to 'self'
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp


def test_emoji_catalog_is_served_as_json(signed_in):
    client, *_ = signed_in
    response = client.get("/static/data/emoji.json")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert [g["id"] for g in data["groups"]][:3] == ["smileys", "people", "nature"]


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", "", source)


def test_javascript_files_do_not_use_innerhtml_or_eval():
    for path in (STATIC / "js").glob("*.js"):
        source = _without_comments(path.read_text())
        assert "innerHTML" not in source, path.name
        assert "insertAdjacentHTML" not in source, path.name
        assert not re.search(r"\beval\(|new Function\(|document\.write\(", source), path.name


def test_hostile_display_names_cannot_break_out_of_attributes_or_markup(signed_in, db):
    client, alice, bob, conversation = signed_in
    hostile = '"><img src=x onerror=alert(1)><script>alert(2)</script>'
    bob.display_name = hostile
    alice.display_name = "'" + hostile
    db.session.commit()

    pages = [
        client.get(f"/chats/{conversation.id}").get_data(as_text=True),
        client.get("/").get_data(as_text=True),
        client.get("/u/bob").get_data(as_text=True),
    ]
    for html in pages:
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert 'onerror=alert(1)>' not in html.replace("&#34;", '"').replace("&gt;", ">") or "&lt;img" in html
    conversation_page = pages[0]
    # the attribute value is entity-escaped, so the quote cannot terminate it
    assert 'data-peer-name="&#34;&gt;&lt;img src=x onerror=alert(1)&gt;&lt;script&gt;alert(2)&lt;/script&gt;"' in conversation_page
    assert 'data-me-name="&#39;&#34;&gt;' in conversation_page


def test_api_serializes_names_as_json_data_not_markup(app, db, make_user):
    me, other = make_user("me"), make_user("other")
    other.display_name = "<b>bold</b>"
    db.session.commit()
    convs.get_or_create_private(me, other)
    client = app.test_client()
    sign_in(client, me)
    body = client.get("/api/conversations").get_json()
    assert body["conversations"][0]["peer"]["display_name"] == "<b>bold</b>"  # raw data; JS uses textContent
    assert client.get("/api/conversations").mimetype == "application/json"
