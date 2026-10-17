"""Find new schemes online with Tavily (owned by P3).

GET  /api/schemes/found        what was found, when the last search ran, whether a key is set
POST /api/schemes/find-new     run the Tavily search now (at most once every 2 minutes)
POST /api/schemes/found/<id>/hide   take a wrong result off the list (demo admin action)
"""
from flask import Blueprint, jsonify, request

from services import tavily_scraper

bp = Blueprint("tavily", __name__)


@bp.get("/schemes/found")
def found():
    return jsonify(tavily_scraper.status())


@bp.post("/schemes/find-new")
def find_new():
    report = tavily_scraper.scrape()
    if not report.get("ok"):
        return jsonify(report), 429 if report.get("wait") else 503
    return jsonify(report)


@bp.post("/schemes/found/<scheme_id>/hide")
def hide(scheme_id):
    hidden = bool((request.get_json(silent=True) or {}).get("hidden", True))
    if not tavily_scraper.set_hidden(scheme_id, hidden):
        return jsonify({"error": "Not a scheme found online."}), 404
    return jsonify({"ok": True, "hidden": hidden})
