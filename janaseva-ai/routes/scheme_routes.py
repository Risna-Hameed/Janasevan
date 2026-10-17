"""Schemes, search and citizen profile endpoints (owned by P1)."""
import json

from flask import Blueprint, jsonify, request

from database.db import get_db, now, row_to_dict
from routes.common import client_id
from services import retrieval

bp = Blueprint("schemes", __name__)

ALLOWED_PROFILE_FIELDS = {
    "name", "age", "state", "district", "education_level", "course", "institution_name", "institution_type",
    "institution_aicte_approved", "year_of_study", "course_mode", "annual_income", "previous_exam_percentage",
    "class12_percentile", "gender", "community", "disability_percent", "bpl", "receives_other_scholarship",
    "preferred_language",
}
NUMERIC = {"age", "annual_income", "previous_exam_percentage", "class12_percentile", "disability_percent", "year_of_study"}
BOOLEAN = {"institution_aicte_approved", "bpl", "receives_other_scholarship"}


def summary(s, lang="en"):
    out = dict(s)
    out["freshness"] = retrieval.freshness(s)
    return out


@bp.get("/schemes")
def list_schemes():
    q = request.args.get("q", "")
    category = request.args.get("category")
    verified_only = request.args.get("verified_only") == "1"
    schemes = [s for _, s in retrieval.search(q, top_k=100)] if q.strip() else retrieval.load_schemes()
    if category:
        schemes = [s for s in schemes if s.get("category") == category]
    if verified_only:
        schemes = [s for s in schemes if s.get("source_status") == "verified"]
    return jsonify({"schemes": [summary(s) for s in schemes]})


@bp.get("/schemes/<scheme_id>")
def get_scheme(scheme_id):
    s = retrieval.get_scheme(scheme_id)
    return jsonify(summary(s)) if s else (jsonify({"error": "Scheme not found"}), 404)


@bp.post("/schemes/search")
def search():
    body = request.get_json(silent=True) or {}
    q = body.get("query", "")
    if not isinstance(q, str):
        return jsonify({"error": "query must be text"}), 400
    return jsonify({"schemes": [summary(s) for _, s in retrieval.search(q, top_k=20)]})


def clean_profile(raw):
    """Validate and keep only known fields. Returns (profile, errors)."""
    profile, errors = {}, {}
    for k, v in (raw or {}).items():
        if k not in ALLOWED_PROFILE_FIELDS or v in (None, ""):
            continue
        if k in NUMERIC:
            try:
                n = float(str(v).replace(",", ""))
            except ValueError:
                errors[k] = "must be a number"
                continue
            if n < 0 or (k == "age" and n > 120) or (k in ("previous_exam_percentage", "class12_percentile", "disability_percent") and n > 100):
                errors[k] = "out of range"
                continue
            profile[k] = int(n) if n.is_integer() else n
        elif k in BOOLEAN:
            profile[k] = v in (True, "true", "yes", "1", 1)
        else:
            profile[k] = str(v).strip()[:120]
    return profile, errors


@bp.post("/profile")
def save_profile():
    cid = client_id()
    if not cid:
        return jsonify({"error": "Missing X-Client-Id header"}), 400
    body = request.get_json(silent=True) or {}
    profile, errors = clean_profile(body.get("profile"))
    if errors:
        return jsonify({"error": "Please check these fields", "fields": errors}), 400
    db = get_db()
    db.execute("""INSERT INTO citizen_profiles (client_id, profile, preferred_language, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?)
                  ON CONFLICT(client_id) DO UPDATE SET profile=excluded.profile,
                  preferred_language=excluded.preferred_language, updated_at=excluded.updated_at""",
               (cid, json.dumps(profile, ensure_ascii=False), profile.get("preferred_language", "en"), now(), now()))
    db.commit()
    return jsonify({"profile": profile})


@bp.get("/profile")
def get_profile():
    cid = client_id()
    if not cid:
        return jsonify({"error": "Missing X-Client-Id header"}), 400
    row = row_to_dict(get_db().execute("SELECT * FROM citizen_profiles WHERE client_id=?", (cid,)).fetchone())
    return jsonify({"profile": row["profile"] if row else {}})


@bp.delete("/profile")
def clear_profile():
    """Clear profile AND everything linked to it (drafts, saved schemes, reminders)."""
    cid = client_id()
    if not cid:
        return jsonify({"error": "Missing X-Client-Id header"}), 400
    db = get_db()
    for table in ("citizen_profiles", "saved_schemes", "application_drafts", "reminders"):
        db.execute(f"DELETE FROM {table} WHERE client_id=?", (cid,))
    db.commit()
    return jsonify({"cleared": True})
