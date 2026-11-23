from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.services import auth_service, conversation_service, message_service
from app.services.presence import tracker

from .serializers import conversation_summary, user_summary

bp = Blueprint("conversations", __name__)


@bp.post("/conversations/start/<username>")
@login_required
def start(username):
    other = auth_service.get_user_by_username(username)
    if other is None or not other.is_active:
        abort(404)
    try:
        conversation, _ = conversation_service.get_or_create_private(
            current_user._get_current_object(), other
        )
    except conversation_service.ConversationError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("profiles.view", username=other.username))
    return redirect(url_for("conversations.view", conversation_id=conversation.id))


@bp.get("/chats/<int:conversation_id>")
@login_required
def view(conversation_id):
    conversation = conversation_service.get_member_conversation(
        conversation_id, current_user.id
    )
    if conversation is None:
        abort(404)  # same answer whether it doesn't exist or isn't yours
    peer = conversation_service.peer_of(conversation.id, current_user.id)
    return render_template(
        "conversation.html",
        conversation=conversation,
        peer=peer,
        peer_online=tracker.is_online(peer.id),
        peer_last_seen=peer.last_seen.isoformat() if peer.last_seen else "",
    )


@bp.get("/api/conversations")
@login_required
def list_conversations():
    items = conversation_service.list_for_user(current_user.id)
    return jsonify(conversations=[conversation_summary(item) for item in items])


@bp.get("/api/conversations/<int:conversation_id>/messages")
@login_required
def messages(conversation_id):
    conversation = conversation_service.get_member_conversation(
        conversation_id, current_user.id
    )
    if conversation is None:
        abort(404)
    peer = conversation_service.peer_of(conversation.id, current_user.id)
    before = request.args.get("before", type=int)
    limit = request.args.get("limit", default=message_service.HISTORY_PAGE_SIZE, type=int)
    page, has_more = message_service.history(
        conversation.id, peer.id, before_id=before, limit=limit
    )
    return jsonify(messages=page, has_more=has_more, peer=user_summary(peer, with_presence=True))
