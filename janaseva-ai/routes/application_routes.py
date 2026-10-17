"""Readiness, drafts, demo confirmation and saved schemes (owned by P1)."""
import json

from datetime import date

from flask import Blueprint, Response, jsonify, request

from database.db import get_db, now, row_to_dict, transaction
from routes.common import client_id
from routes.scheme_routes import clean_profile
from services import application_service, application_submission, readiness_engine, retrieval

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
    version = 1
    if body.get("draft_id"):
        old = db.execute("SELECT * FROM application_drafts WHERE id=? AND client_id=?", (body["draft_id"], cid)).fetchone()
        if not old:
            return jsonify({"error": "Draft not found"}), 404
        if old["status"] == "demo_confirmed":
            return jsonify({"error": "This application has already been submitted and can no longer be edited."}), 409
        version = old["version"] + 1          # every saved change is a new version that must be previewed again
        notes = json.loads(old["change_notes"] or "[]")
        note = (body.get("change_note") or "").strip()[:1000] if isinstance(body.get("change_note"), str) else ""
        if note:
            notes.append({"version": version, "note": note, "at": now()})
        db.execute("""UPDATE application_drafts SET form_data=?, provided_items=?, missing_items=?,
                      readiness_score=?, updated_at=?, status='draft', version=?, change_notes=? WHERE id=? AND client_id=?""",
                   (*values, version, json.dumps(notes, ensure_ascii=False), body["draft_id"], cid))
        draft_id = body["draft_id"]
    else:
        cur = db.execute("""INSERT INTO application_drafts (client_id, scheme_id, form_data, provided_items,
                            missing_items, readiness_score, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                         (cid, body["scheme_id"], values[0], values[1], values[2], values[3], now(), now()))
        draft_id = cur.lastrowid
    db.commit()
    return jsonify({**draft, "draft_id": draft_id, "status": "draft", "version": version})


# ---------------------------------------------------------------- submit flow: check -> preview -> (edit) -> submit -> final PDF
def _own_draft(db, cid, draft_id):
    row = db.execute("SELECT * FROM application_drafts WHERE id=? AND client_id=?", (draft_id, cid)).fetchone()
    return row_to_dict(row) if row else None


@bp.post("/application/<int:draft_id>/check")
def check_draft(draft_id):
    """What, if anything, stops this application being submitted."""
    cid, err = _need_client()
    if err:
        return err
    db = get_db()
    d = _own_draft(db, cid, draft_id)
    if not d:
        return jsonify({"error": "Draft not found"}), 404
    scheme = retrieval.get_scheme(d["scheme_id"])
    profile, _ = clean_profile((request.get_json(silent=True) or {}).get("profile"))
    result = application_submission.check(scheme, d["form_data"], d["provided_items"], profile)
    return jsonify({**result, "draft_id": draft_id, "version": d["version"], "status": d["status"]})


def _verification_records(db, cid):
    rows = db.execute("SELECT r.request_ref, r.document_category, v.receipt_ref FROM verification_requests r "
                      "JOIN verification_reviews v ON v.request_id=r.id AND v.kind='final' AND v.outcome='verified' "
                      "WHERE r.client_id=? AND r.status='verified'", (cid,)).fetchall()
    from services.verification_workflow import DOCUMENT_CATEGORIES
    return [{"request_ref": r["request_ref"], "receipt_ref": r["receipt_ref"],
             "category": DOCUMENT_CATEGORIES.get(r["document_category"], {}).get("name_en", r["document_category"])} for r in rows]


@bp.get("/application/<int:draft_id>/pdf")
def application_pdf(draft_id):
    """?kind=preview (only before submission) or ?kind=final (only after). ?download=1 for a file download."""
    cid, err = _need_client()
    if err:
        return err
    db = get_db()
    d = _own_draft(db, cid, draft_id)
    if not d:
        return jsonify({"error": "Draft not found"}), 404
    kind = request.args.get("kind", "preview")
    if kind == "final" and d["status"] != "demo_confirmed":
        return jsonify({"error": "The final application is available only after submission."}), 409
    if kind == "preview" and d["status"] == "demo_confirmed":
        kind = "final"
    if kind == "preview":
        db.execute("UPDATE application_drafts SET previewed_version=? WHERE id=?", (d["version"], draft_id))  # student has seen this version
    pdf = application_submission.build_pdf(d, retrieval.get_scheme(d["scheme_id"]), kind, _verification_records(db, cid))
    name = f"{d['application_ref']}.pdf" if kind == "final" else f"application-preview-v{d['version']}.pdf"
    disp = "attachment" if request.args.get("download") == "1" else "inline"
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "no-store"})


@bp.post("/application/<int:draft_id>/submit")
def submit_application(draft_id):
    """DEMO submission. Requires: no blockers, the latest version previewed, and explicit confirmation."""
    cid, err = _need_client()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    db = get_db()
    d = _own_draft(db, cid, draft_id)
    if not d:
        return jsonify({"error": "Draft not found"}), 404
    if d["status"] == "demo_confirmed":
        return jsonify({"error": "This application was already submitted.", "application_ref": d["application_ref"]}), 409
    if body.get("confirmed") is not True:
        return jsonify({"error": "Please confirm that you have reviewed the whole application."}), 400
    if d["previewed_version"] != d["version"]:
        return jsonify({"error": "Please view the latest preview of your application before submitting.", "needs_preview": True}), 409
    profile, _ = clean_profile(body.get("profile"))
    result = application_submission.check(retrieval.get_scheme(d["scheme_id"]), d["form_data"], d["provided_items"], profile)
    if not result["ready"]:
        return jsonify({"error": "The application is not complete yet.", **result}), 409
    with transaction(db):
        ref = f"JSA-APP-{date.today().year}-{draft_id:05d}"
        cur = db.execute("UPDATE application_drafts SET status='demo_confirmed', application_ref=?, submitted_at=?, updated_at=? "
                         "WHERE id=? AND client_id=? AND status='draft'", (ref, now(), now(), draft_id, cid))
        if cur.rowcount == 0:
            return jsonify({"error": "This application could not be submitted. Please reload."}), 409
    d = _own_draft(db, cid, draft_id)
    return jsonify({"draft_id": draft_id, "status": "submitted", "application_ref": ref, "submitted_at": d["submitted_at"],
                    "version": d["version"],
                    "message_en": "Application submitted (demo). DEMO APPLICATION ONLY — no application has been sent to any government department.",
                    "message_ml": "അപേക്ഷ സമർപ്പിച്ചു (ഡെമോ). ഒരു സർക്കാർ വകുപ്പിലേക്കും അപേക്ഷ അയച്ചിട്ടില്ല.",
                    "next_step_en": "Use your final application PDF when applying through the official portal or Akshaya centre.",
                    "next_step_ml": "ഔദ്യോഗിക പോർട്ടലിലോ അക്ഷയ കേന്ദ്രത്തിലോ അപേക്ഷിക്കുമ്പോൾ അന്തിമ അപേക്ഷ PDF ഉപയോഗിക്കുക."})


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
