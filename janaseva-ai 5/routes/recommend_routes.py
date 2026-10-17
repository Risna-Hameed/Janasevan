"""Eligibility + BestFit ranking endpoints."""
from flask import Blueprint, jsonify, request

from services import eligibility_engine, ranker

bp = Blueprint("recommend", __name__)


def _profile(body):
    p = body.get("profile")
    return p if isinstance(p, dict) else None


@bp.post("/eligibility/check")
def check():
    """{profile, scheme_id} -> explained eligibility result"""
    body = request.get_json(silent=True) or {}
    if _profile(body) is None or not isinstance(body.get("scheme_id"), str):
        return jsonify({"error": "profile (object) and scheme_id (text) are required"}), 400
    result = eligibility_engine.check_eligibility(body["profile"], body["scheme_id"])
    return (jsonify(result), 404) if result.get("error") else jsonify(result)


@bp.post("/eligibility/match")
def match():
    """{profile} -> every scheme with its status, best first"""
    body = request.get_json(silent=True) or {}
    if _profile(body) is None:
        return jsonify({"error": "profile (object) is required"}), 400
    return jsonify({"results": eligibility_engine.match_all(body["profile"])})


@bp.post("/recommend/best")
def best():
    """{profile, provided_docs: [doc ids], preset: balanced|money|easy|sure} -> ranked list with reasons"""
    body = request.get_json(silent=True) or {}
    if _profile(body) is None:
        return jsonify({"error": "profile (object) is required"}), 400
    docs = body.get("provided_docs") if isinstance(body.get("provided_docs"), list) else []
    preset = body.get("preset") if body.get("preset") in ranker.WEIGHT_PRESETS else "balanced"
    return jsonify(ranker.rank(body["profile"], docs, preset))
