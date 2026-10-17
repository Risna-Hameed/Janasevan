"""Citizen side of document verification.

DEMO IDENTITY (clearly not production authentication):
  The browser's random client id (X-Client-Id) is linked to a household using the household
  reference + a one-time link code printed on the survey slip. The panchayat that receives a
  request is taken from that household record on the server - the citizen cannot choose it.
  Production needs proper citizen sign-in (e.g. an approved government identity provider).
"""
import time
from collections import defaultdict

from flask import Blueprint, Response, jsonify, request
from werkzeug.security import check_password_hash

from database.db import get_db, now, transaction
from routes.common import client_id
from services import private_files, retrieval
from services.panchayat_auth import audit, licence_for
from services.verification_workflow import (DOCUMENT_CATEGORIES, WorkflowError, citizen_view, get_request, make_ref,
                                            notify_citizen, notify_officials, transition)

bp = Blueprint("citizen_verification", __name__)
_link_attempts = defaultdict(list)


def _cid():
    cid = client_id()
    return cid, (None if cid else (jsonify({"error": "Missing X-Client-Id header"}), 400))


def _link(db, cid):
    return db.execute("SELECT * FROM citizen_links WHERE client_id=?", (cid,)).fetchone()


def _link_payload(db, link):
    h = db.execute("SELECT h.household_ref, w.number AS ward, w.name AS ward_name, p.name AS panchayat, p.is_demo "
                   "FROM households h JOIN wards w ON w.id=h.ward_id JOIN panchayats p ON p.id=h.panchayat_id WHERE h.id=?",
                   (link["household_id"],)).fetchone()
    members = [{"member_ref": m["member_ref"], "name": m["name"]} for m in db.execute(
        "SELECT member_ref, name FROM household_members WHERE household_id=? ORDER BY id", (link["household_id"],))]
    return {"linked": True, "household_ref": h["household_ref"], "ward": f"{h['ward']} - {h['ward_name']}",
            "panchayat": h["panchayat"], "is_demo": bool(h["is_demo"]), "members": members,
            "identity_note": "Demo identity: this browser is linked to the household. Not production authentication."}


@bp.get("/link")
def get_link():
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    link = _link(db, cid)
    return jsonify(_link_payload(db, link) if link else {"linked": False})


@bp.post("/link")
def create_link():
    cid, err = _cid()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    ref, code = (body.get("household_ref") or "").strip().upper(), (body.get("link_code") or "").strip()
    key = (request.remote_addr, cid)
    _link_attempts[key] = [t for t in _link_attempts[key] if time.time() - t < 300]
    if len(_link_attempts[key]) >= 5:
        return jsonify({"error": "Too many attempts. Please wait 5 minutes."}), 429
    db = get_db()
    h = db.execute("SELECT * FROM households WHERE household_ref=?", (ref,)).fetchone()
    if not h or not h["citizen_link_code_hash"] or not check_password_hash(h["citizen_link_code_hash"], code):
        _link_attempts[key].append(time.time())
        return jsonify({"error": "Household reference or link code is incorrect."}), 400
    with transaction(db):
        db.execute("INSERT INTO citizen_links (client_id, household_id, panchayat_id, linked_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(client_id) DO UPDATE SET household_id=excluded.household_id, panchayat_id=excluded.panchayat_id, linked_at=excluded.linked_at",
                   (cid, h["id"], h["panchayat_id"], now()))
        audit("citizen_linked", "household", h["id"], panchayat_id=h["panchayat_id"], actor_type="citizen", actor_id=cid[:8], db=db)
    return jsonify(_link_payload(db, _link(db, cid)))


@bp.delete("/link")
def delete_link():
    cid, err = _cid()
    if err:
        return err
    get_db().execute("DELETE FROM citizen_links WHERE client_id=?", (cid,))
    return jsonify({"linked": False})


@bp.get("/document-categories")
def categories():
    return jsonify({"categories": [{"code": k, "name_en": v["name_en"], "name_ml": v["name_ml"],
                                    "panchayat_can_check": v["scope"] == "record_check", "note_en": v["note_en"]}
                                   for k, v in DOCUMENT_CATEGORIES.items()]})


@bp.post("/verification-requests")
def submit_request():
    """multipart: file, document_category, purpose, scheme_id?, member_ref?, doc_reference?, issuing_authority?, valid_until?, note?"""
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    link = _link(db, cid)
    if not link:
        return jsonify({"error": "Link your household first (household reference + link code from your survey slip)."}), 403
    f = request.form
    category = f.get("document_category")
    purpose = (f.get("purpose") or "").strip()[:200]
    if category not in DOCUMENT_CATEGORIES:
        return jsonify({"error": "Choose a valid document type."}), 400
    if len(purpose) < 3:
        return jsonify({"error": "Tell us what the verification is for (e.g. the scholarship name)."}), 400
    scheme_id = f.get("scheme_id") or None
    if scheme_id and not retrieval.get_scheme(scheme_id):
        return jsonify({"error": "Unknown scheme."}), 400
    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "Attach the document (PDF, PNG or JPG, up to 5 MB)."}), 400
    # jurisdiction comes from the household record on the server, never from the form
    h = db.execute("SELECT * FROM households WHERE id=?", (link["household_id"],)).fetchone()
    if not licence_for(h["panchayat_id"])["valid"]:
        return jsonify({"error": "Your panchayat is not currently accepting online verification requests."}), 503
    member_id = None
    if f.get("member_ref"):
        m = db.execute("SELECT id FROM household_members WHERE member_ref=? AND household_id=?", (f["member_ref"], h["id"])).fetchone()
        if not m:
            return jsonify({"error": "That person is not in your household record."}), 400
        member_id = m["id"]
    valid_until = (f.get("valid_until") or "").strip()[:10] or None
    try:
        stored = private_files.save(h["panchayat_id"], upload.filename, upload.read())
    except private_files.UploadError as e:
        return jsonify({"error": str(e)}), 400
    with transaction(db):
        d = db.execute("INSERT INTO documents (panchayat_id, client_id, storage_name, original_name, mime_type, size_bytes, sha256, "
                       "doc_reference, issuing_authority, valid_until, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (h["panchayat_id"], cid, stored["storage_name"], stored["original_name"], stored["mime_type"], stored["size_bytes"],
                        stored["sha256"], (f.get("doc_reference") or "").strip()[:60] or None,
                        (f.get("issuing_authority") or "").strip()[:100] or None, valid_until, now()))
        cur = db.execute("INSERT INTO verification_requests (request_ref, client_id, household_id, member_id, panchayat_id, ward_id, "
                         "document_category, purpose, scheme_id, status, citizen_remarks, is_demo, submitted_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?)",
                         ("pending", cid, h["id"], member_id, h["panchayat_id"], h["ward_id"], category, purpose, scheme_id,
                          (f.get("note") or "").strip()[:500] or None, h["is_demo"], now(), now()))
        rid = cur.lastrowid
        ref = make_ref("VER", rid)
        db.execute("UPDATE verification_requests SET request_ref=? WHERE id=?", (ref, rid))
        db.execute("INSERT INTO verification_request_documents (request_id, document_id, round, added_at) VALUES (?, ?, 1, ?)",
                   (rid, d.lastrowid, now()))
        db.execute("INSERT INTO verification_history (request_id, from_status, to_status, actor_type, actor_id, note, created_at) "
                   "VALUES (?, NULL, 'submitted', 'citizen', ?, 'Submitted from the citizen portal', ?)", (rid, cid[:8], now()))
        req = get_request(db, rid)
        transition(db, req, "queued_for_review", "system", "router", "Routed to the household's panchayat")
        req = get_request(db, rid)
        notify_citizen(db, req, "queued_for_review")
        notify_officials(db, req, f"New {DOCUMENT_CATEGORIES[category]['name_en'].lower()} verification request.",
                         "പുതിയ രേഖ പരിശോധനാ അപേക്ഷ.")
        audit("verification_submitted", "verification_request", rid, panchayat_id=h["panchayat_id"], actor_type="citizen", actor_id=cid[:8], db=db)
    return jsonify(citizen_view(db, get_request(db, rid))), 201


def _own(db, cid, rid):
    req = get_request(db, rid)
    return req if req and req["client_id"] == cid else None


@bp.get("/verification-requests")
def my_requests():
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    rows = db.execute("SELECT * FROM verification_requests WHERE client_id=? ORDER BY id DESC", (cid,)).fetchall()
    return jsonify({"items": [citizen_view(db, dict(r)) for r in rows]})


@bp.get("/verification-requests/<int:rid>")
def my_request(rid):
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    req = _own(db, cid, rid)
    return jsonify(citizen_view(db, req)) if req else (jsonify({"error": "Request not found"}), 404)


@bp.post("/verification-requests/<int:rid>/resubmit")
def resubmit(rid):
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    req = _own(db, cid, rid)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    if req["status"] != "info_required":
        return jsonify({"error": "This request is not waiting for more information."}), 409
    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "Attach the requested document."}), 400
    try:
        stored = private_files.save(req["panchayat_id"], upload.filename, upload.read())
    except private_files.UploadError as e:
        return jsonify({"error": str(e)}), 400
    rnd = req["resubmission_count"] + 2
    with transaction(db):
        d = db.execute("INSERT INTO documents (panchayat_id, client_id, storage_name, original_name, mime_type, size_bytes, sha256, uploaded_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (req["panchayat_id"], cid, stored["storage_name"], stored["original_name"],
                                                           stored["mime_type"], stored["size_bytes"], stored["sha256"], now()))
        # earlier documents are kept - only a new round is added
        db.execute("INSERT INTO verification_request_documents (request_id, document_id, round, added_at) VALUES (?, ?, ?, ?)",
                   (rid, d.lastrowid, rnd, now()))
        db.execute("UPDATE verification_requests SET resubmission_count=resubmission_count+1, citizen_remarks=COALESCE(?, citizen_remarks), "
                   "assigned_official_id=NULL WHERE id=?", ((request.form.get("note") or "").strip()[:500] or None, rid))
        transition(db, req, "queued_for_review", "citizen", cid[:8], "Citizen uploaded the requested information")
        req = get_request(db, rid)
        notify_citizen(db, req, "queued_for_review")
        notify_officials(db, req, "Citizen resubmitted the requested information.", "പൗരൻ ആവശ്യപ്പെട്ട വിവരങ്ങൾ വീണ്ടും സമർപ്പിച്ചു.")
        audit("verification_resubmitted", "verification_request", rid, panchayat_id=req["panchayat_id"], actor_type="citizen", actor_id=cid[:8], db=db)
    return jsonify(citizen_view(db, get_request(db, rid)))


@bp.post("/verification-requests/<int:rid>/cancel")
def cancel(rid):
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    req = _own(db, cid, rid)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    try:
        with transaction(db):
            transition(db, req, "cancelled", "citizen", cid[:8], "Cancelled by citizen")
            notify_citizen(db, get_request(db, rid), "cancelled")
    except WorkflowError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(citizen_view(db, get_request(db, rid)))


@bp.get("/verification-requests/<int:rid>/receipt")
def receipt(rid):
    from routes.panchayat_routes import build_receipt_pdf
    cid, err = _cid()
    if err:
        return err
    db = get_db()
    req = _own(db, cid, rid)
    if not req or req["status"] != "verified":
        return jsonify({"error": "No verification record is available for this request."}), 404
    return Response(build_receipt_pdf(db, req), mimetype="application/pdf", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{req["request_ref"]}-record.pdf"'})


@bp.get("/notifications")
def my_notifications():
    cid, err = _cid()
    if err:
        return err
    rows = get_db().execute("SELECT * FROM notifications WHERE recipient_type='citizen' AND client_id=? ORDER BY id DESC LIMIT 30", (cid,)).fetchall()
    items = [{"id": r["id"], "title_en": r["title_en"], "title_ml": r["title_ml"], "body_en": r["body_en"], "body_ml": r["body_ml"],
              "request_id": r["request_id"], "is_read": bool(r["is_read"]), "created_at": r["created_at"]} for r in rows]
    return jsonify({"items": items, "unread": sum(1 for i in items if not i["is_read"])})


@bp.post("/notifications/read-all")
def read_all():
    cid, err = _cid()
    if err:
        return err
    get_db().execute("UPDATE notifications SET is_read=1 WHERE recipient_type='citizen' AND client_id=?", (cid,))
    return jsonify({"ok": True})
