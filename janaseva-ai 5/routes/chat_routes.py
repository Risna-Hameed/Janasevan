"""POST /api/chat  (owned by P3)."""
from flask import Blueprint, jsonify, request

from services import chatbot

bp = Blueprint("chat", __name__)


@bp.post("/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = body.get("message")
    if not isinstance(message, str) or len(message) > 1000:
        return jsonify({"error": "message must be text of at most 1000 characters"}), 400
    lang = body.get("lang") if body.get("lang") in ("en", "ml") else None
    profile = body.get("profile") if isinstance(body.get("profile"), dict) else {}
    scheme_id = body.get("scheme_id") if isinstance(body.get("scheme_id"), str) else None
    return jsonify(chatbot.answer(message, lang=lang, profile=profile, scheme_id=scheme_id))
