"""Readiness, drafts, demo confirmation and saved schemes (owned by P1)."""
import json

from flask import Blueprint, jsonify, request

from database.db import get_db, now, row_to_dict
from routes.common import client_id
from routes.scheme_routes import clean_profile
from services import application_service, readiness_engine, retrieval

bp = Blueprint("applications", __name__)


def _need_client():
    cid = client_id()
    return cid, (None if cid else (jsonify({"error": "Missing X-Client-Id header"}), 400))


@bp.post("/application/readiness")
def readiness():
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("scheme_id"), str):
        return jsonify({"error": "scheme_id is required"}), 400
    items = body.get("provided_items") if isinstance(body.get("provided_items"), list) else []
    profile, _ = clean_profile(body.get("profile"))
    result = readiness_engine.compute(body["scheme_id"], items, profile)
    return jsonify(result) if result else (jsonify({"error": "Scheme not found"}), 404)


@bp.post("/application/draft")
def save_draft():
    """Create (no draft_id) or update (with draft_id) a draft. Body: {scheme_id, profile, provided_items, edits, draft_id?}"""
    cid, err = _need_client()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("scheme_id"), str):
        return jsonify({"error": "scheme_id is required"}), 400
    profile, errors = clean_profile(body.get("profile"))
    edits, edit_errors = clean_profile(body.get("edits"))
    if errors or edit_errors:
        return jsonify({"error": "Please check these fields", "fields": {**errors, **edit_errors}}), 400
    items = [i for i in (body.get("provided_items") or []) if isinstance(i, str)]
    draft = application_service.build(body["scheme_id"], profile, items, edits)
    if not draft:
        return jsonify({"error": "Scheme not found"}), 404

    db = get_db()
    values = (json.dumps(draft["form_data"], ensure_ascii=False), json.dumps(items),
              json.dumps(draft["missing_items"]), draft["readiness_score"], now())
    if body.get("draft_id"):
        cur = db.execute("""UPDATE application_drafts SET form_data=?, provided_items=?, missing_items=?,
                            readiness_score=?, updated_at=?, status='draft' WHERE id=? AND client_id=?""",
                         (*values, body["draft_id"], cid))
        if cur.rowcount == 0:
            return jsonify({"error": "Draft not found"}), 404
        draft_id = body["draft_id"]
    else:
        cur = db.execute("""INSERT INTO application_drafts (client_id, scheme_id, form_data, provided_items,
                            missing_items, readiness_score, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                         (cid, body["scheme_id"], values[0], values[1], values[2], values[3], now(), now()))
        draft_id = cur.lastrowid
    db.commit()
    return jsonify({**draft, "draft_id": draft_id, "status": "draft"})


@bp.post("/application/confirm")
def confirm():
    """Demo confirmation only. Requires explicit consent; never contacts any government system."""
    cid, err = _need_client()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return jsonify({"error": "Please tick the confirmation box first."}), 400
    db = get_db()
    cur = db.execute("UPDATE application_drafts SET status='demo_confirmed', updated_at=? WHERE id=? AND client_id=?",
                     (now(), body.get("draft_id"), cid))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Draft not found"}), 404
    return jsonify({"draft_id": body["draft_id"], "status": "demo_confirmed",
                    "message_en": "DEMO APPLICATION ONLY — No application has been submitted to a government department.",
                    "message_ml": "ഡെമോ അപേക്ഷ മാത്രം — ഒരു സർക്കാർ വകുപ്പിലേക്കും അപേക്ഷ സമർപ്പിച്ചിട്ടില്ല."})


@bp.get("/application/<int:draft_id>")
def get_draft(draft_id):
    cid, err = _need_client()
    if err:
        return err
    row = row_to_dict(get_db().execute("SELECT * FROM application_drafts WHERE id=? AND client_id=?", (draft_id, cid)).fetchone())
    return jsonify(row) if row else (jsonify({"error": "Draft not found"}), 404)


@bp.get("/application")
def list_drafts():
    cid, err = _need_client()
    if err:
        return err
    rows = get_db().execute("SELECT * FROM application_drafts WHERE client_id=? ORDER BY updated_at DESC", (cid,)).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        s = retrieval.get_scheme(d["scheme_id"]) or {}
        d["scheme_name_en"], d["scheme_name_ml"] = s.get("name_en"), s.get("name_ml")
        out.append(d)
    return jsonify({"drafts": out})


@bp.get("/saved")
def list_saved():
    cid, err = _need_client()
    if err:
        return err
    rows = get_db().execute("SELECT scheme_id FROM saved_schemes WHERE client_id=? ORDER BY created_at DESC", (cid,)).fetchall()
    return jsonify({"saved": [r["scheme_id"] for r in rows]})


@bp.post("/saved")
def toggle_saved():
    cid, err = _need_client()
    if err:
        return err
    sid = (request.get_json(silent=True) or {}).get("scheme_id")
    if not retrieval.get_scheme(sid or ""):
        return jsonify({"error": "Scheme not found"}), 404
    db = get_db()
    if db.execute("SELECT 1 FROM saved_schemes WHERE client_id=? AND scheme_id=?", (cid, sid)).fetchone():
        db.execute("DELETE FROM saved_schemes WHERE client_id=? AND scheme_id=?", (cid, sid))
        saved = False
    else:
        db.execute("INSERT INTO saved_schemes (client_id, scheme_id, created_at) VALUES (?, ?, ?)", (cid, sid, now()))
        saved = True
    db.commit()
    return jsonify({"scheme_id": sid, "saved": saved})
