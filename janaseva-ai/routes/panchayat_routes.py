"""Panchayat Dashboard: page, sign-in, dashboard, verification requests, documents, notifications.

Every endpoint: signed-in official -> permission -> licence -> CSRF (writes) -> jurisdiction filter
in the SQL itself. A record outside the official's scope returns 404 (not 403) so its
existence is not revealed.
"""
import json
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, session

from database.db import get_db, now, transaction
from services import document_service, private_files, retrieval
from services import verification_receipt
from services.panchayat_auth import (ROLE_LABELS, audit, current_official, in_scope, licence_for, login, logout,
                                     require_official, scope_sql)
from services.verification_workflow import (CLOSED_STATUSES, CORRECTION_REASONS, DOCUMENT_CATEGORIES, OPEN_STATUSES,
                                            REFERRAL_CATEGORIES, STATUSES, UNSUCCESSFUL_REASONS, WorkflowError,
                                            checklist_for, dumps, make_ref, notify_citizen, transition)

page_bp = Blueprint("panchayat_page", __name__)
bp = Blueprint("panchayat", __name__)


@page_bp.get("/panchayat")
def panchayat_page():
    return render_template("panchayat.html")


# ---------------------------------------------------------------- auth
@bp.post("/login")
def do_login():
    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
        return jsonify({"error": "Username and password are required."}), 400
    row, err, code = login(username, password)
    if err:
        return jsonify({"error": err}), code
    return jsonify(_me_payload())


@bp.post("/logout")
@require_official()
def do_logout():
    logout()
    return jsonify({"signed_out": True})


def _me_payload():
    off = current_official()
    db = get_db()
    p = db.execute("SELECT p.*, b.name AS block_name, b.district FROM panchayats p JOIN blocks b ON b.id=p.block_id WHERE p.id=?",
                   (off["panchayat_id"],)).fetchone()
    wards = [dict(w) for w in db.execute("SELECT id, number, name FROM wards WHERE panchayat_id=? ORDER BY number", (off["panchayat_id"],))
             if off["ward_ids"] is None or w["id"] in off["ward_ids"]]
    return {"official": {"id": off["id"], "username": off["username"], "full_name": off["full_name"], "role": off["role"],
                         "role_label": ROLE_LABELS[off["role"]], "permissions": off["permissions"], "is_demo": bool(off["is_demo"])},
            "panchayat": {"id": p["id"], "name": p["name"], "code": p["code"], "block": p["block_name"], "district": p["district"],
                          "is_demo": bool(p["is_demo"])},
            "wards": wards, "all_wards": off["ward_ids"] is None, "licence": licence_for(off["panchayat_id"]),
            "csrf_token": session.get("csrf"),
            "categories": {k: {"name_en": v["name_en"], "name_ml": v["name_ml"], "scope": v["scope"]} for k, v in DOCUMENT_CATEGORIES.items()},
            "statuses": {k: v[0] for k, v in STATUSES.items()},
            "correction_reasons": CORRECTION_REASONS, "unsuccessful_reasons": UNSUCCESSFUL_REASONS,
            "referral_categories": REFERRAL_CATEGORIES,
            "processing_target_days": current_app.config["PROCESSING_TARGET_DAYS"]}


@bp.get("/me")
@require_official()
def me():
    return jsonify(_me_payload())


# ---------------------------------------------------------------- dashboard
def _overdue_cutoff():
    days = current_app.config["PROCESSING_TARGET_DAYS"]
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


@bp.get("/dashboard")
@require_official("view_dashboard")
def dashboard():
    db, off = get_db(), g.official
    hs, hp = scope_sql("h")
    rs, rp = scope_sql("r")
    counts = dict(db.execute(f"SELECT r.status, COUNT(*) FROM verification_requests r WHERE {rs} GROUP BY r.status", rp).fetchall())
    households = db.execute(f"SELECT COUNT(*) FROM households h WHERE {hs}", hp).fetchone()[0]
    individuals = db.execute(f"SELECT COUNT(*) FROM household_members m JOIN households h ON h.id=m.household_id WHERE {hs}", hp).fetchone()[0]
    wards = len(_me_payload()["wards"])
    overdue = db.execute(f"SELECT COUNT(*) FROM verification_requests r WHERE {rs} AND r.status IN ({','.join('?'*len(OPEN_STATUSES))}) "
                         f"AND r.submitted_at < ?", rp + list(OPEN_STATUSES) + [_overdue_cutoff()]).fetchone()[0]
    recent = _request_rows(f"WHERE {rs}", rp, "ORDER BY r.submitted_at DESC LIMIT 6")
    attention = _request_rows(f"WHERE {rs} AND (r.status IN ('submitted','queued_for_review') AND (r.submitted_at < ? OR r.resubmission_count > 0))",
                              rp + [_overdue_cutoff()], "ORDER BY r.submitted_at ASC LIMIT 6")
    activity = [dict(a) for a in db.execute(
        "SELECT a.action, a.target_type, a.target_id, a.created_at, COALESCE(o.full_name, a.actor_type) AS actor "
        "FROM audit_logs a LEFT JOIN official_users o ON a.actor_type='official' AND o.id = CAST(a.actor_id AS INTEGER) "
        "WHERE a.panchayat_id=? AND a.action NOT IN ('login_failed') ORDER BY a.id DESC LIMIT 8", (off["panchayat_id"],))]
    by_cat = dict(db.execute(f"SELECT r.document_category, COUNT(*) FROM verification_requests r WHERE {rs} GROUP BY 1", rp).fetchall())
    by_ward = [dict(x) for x in db.execute(
        f"SELECT w.number AS ward, COUNT(r.id) AS n FROM wards w LEFT JOIN verification_requests r ON r.ward_id=w.id "
        f"WHERE w.panchayat_id=? GROUP BY w.id ORDER BY w.number", (off["panchayat_id"],))]
    since = (date.today() - timedelta(days=13)).isoformat()
    daily = dict(db.execute(
        f"SELECT substr(hh.created_at,1,10) d, COUNT(*) FROM verification_history hh JOIN verification_requests r ON r.id=hh.request_id "
        f"WHERE {rs} AND hh.actor_type='official' AND substr(hh.created_at,1,10) >= ? GROUP BY d", rp + [since]).fetchall())
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    if off["ward_ids"] is not None:
        allowed = {w["number"] for w in _me_payload()["wards"]}
        by_ward = [w for w in by_ward if w["ward"] in allowed]
    return jsonify({
        "stats": {"households": households, "individuals": individuals, "wards": wards,
                  "new_requests": counts.get("submitted", 0) + counts.get("queued_for_review", 0),
                  "awaiting_review": counts.get("queued_for_review", 0) + counts.get("under_review", 0),
                  "under_review": counts.get("under_review", 0),
                  "info_required": counts.get("info_required", 0), "verified": counts.get("verified", 0),
                  "unsuccessful_or_referred": counts.get("unsuccessful", 0) + counts.get("referred", 0),
                  "overdue": overdue},
        "recent_requests": recent, "attention": attention, "activity": activity,
        "charts": {"by_status": {STATUSES[k][0]: v for k, v in counts.items()},
                   "by_category": {DOCUMENT_CATEGORIES.get(k, {}).get("name_en", k): v for k, v in by_cat.items()},
                   "by_ward": {f"Ward {w['ward']}": w["n"] for w in by_ward},
                   "activity": {"labels": days, "values": [daily.get(d, 0) for d in days]}},
        "data_note": "Figures are calculated from the database. Demo panchayats contain fictional records.",
    })


# ---------------------------------------------------------------- verification requests
def _request_rows(where, params, tail):
    db = get_db()
    rows = db.execute(
        "SELECT r.*, w.number AS ward_number, m.name AS applicant_name, h.household_ref, o.full_name AS assigned_name "
        "FROM verification_requests r JOIN wards w ON w.id=r.ward_id JOIN households h ON h.id=r.household_id "
        "LEFT JOIN household_members m ON m.id=r.member_id LEFT JOIN official_users o ON o.id=r.assigned_official_id "
        f"{where} {tail}", params).fetchall()
    cutoff = _overdue_cutoff()
    return [{"id": r["id"], "request_ref": r["request_ref"], "citizen_ref": "CIT-" + r["client_id"][:8].upper(),
             "applicant_name": r["applicant_name"] or "-", "household_ref": r["household_ref"], "ward": r["ward_number"],
             "document_category": r["document_category"],
             "category_en": DOCUMENT_CATEGORIES.get(r["document_category"], {}).get("name_en", r["document_category"]),
             "purpose": r["purpose"], "scheme_id": r["scheme_id"], "submitted_at": r["submitted_at"], "updated_at": r["updated_at"],
             "status": r["status"], "status_en": STATUSES[r["status"]][0], "assigned_official": r["assigned_name"],
             "assigned_official_id": r["assigned_official_id"], "resubmission_count": r["resubmission_count"],
             "overdue": r["status"] in OPEN_STATUSES and r["submitted_at"] < cutoff, "is_demo": bool(r["is_demo"])}
            for r in rows]


@bp.get("/verification-requests")
@require_official("view_requests", feature="verification")
def list_requests():
    a = request.args
    where, params = scope_sql("r")
    where = "WHERE " + where
    if a.get("q"):
        where += " AND (r.request_ref LIKE ? OR m.name LIKE ? OR h.household_ref LIKE ?)"
        q = f"%{a['q'].strip()[:60]}%"
        params += [q, q, q]
    if a.get("ward"):
        where += " AND w.number = ?"; params.append(_int(a["ward"]))
    if a.get("category") in DOCUMENT_CATEGORIES:
        where += " AND r.document_category = ?"; params.append(a["category"])
    if a.get("status") in STATUSES:
        where += " AND r.status = ?"; params.append(a["status"])
    elif a.get("status") == "open":
        where += f" AND r.status IN ({','.join('?'*len(OPEN_STATUSES))})"; params += list(OPEN_STATUSES)
    elif a.get("status") == "closed":
        where += f" AND r.status IN ({','.join('?'*len(CLOSED_STATUSES))})"; params += list(CLOSED_STATUSES)
    if a.get("from"):
        where += " AND substr(r.submitted_at,1,10) >= ?"; params.append(a["from"][:10])
    if a.get("to"):
        where += " AND substr(r.submitted_at,1,10) <= ?"; params.append(a["to"][:10])
    sort = {"date_desc": "r.submitted_at DESC", "date_asc": "r.submitted_at ASC", "status": "r.status, r.submitted_at DESC"}.get(a.get("sort"), "r.submitted_at DESC")
    page, size = max(1, _int(a.get("page"), 1)), min(50, max(5, _int(a.get("page_size"), 10)))
    base_from = ("FROM verification_requests r JOIN wards w ON w.id=r.ward_id JOIN households h ON h.id=r.household_id "
                 "LEFT JOIN household_members m ON m.id=r.member_id ")
    total = get_db().execute(f"SELECT COUNT(*) {base_from} {where}", params).fetchone()[0]
    rows = _request_rows(where, params, f"ORDER BY {sort} LIMIT {size} OFFSET {(page - 1) * size}")
    return jsonify({"items": rows, "total": total, "page": page, "page_size": size, "pages": max(1, -(-total // size))})


def _load_scoped(request_id):
    """Fetch a request only if it is inside the official's jurisdiction, else None."""
    req = get_db().execute("SELECT * FROM verification_requests WHERE id=?", (request_id,)).fetchone()
    if not req or not in_scope(req["panchayat_id"], req["ward_id"]):
        if req:
            audit("cross_scope_attempt", "verification_request", request_id)
        return None
    return dict(req)


@bp.get("/verification-requests/<int:request_id>")
@require_official("view_requests", feature="verification")
def get_request_detail(request_id):
    req = _load_scoped(request_id)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    db, off = get_db(), g.official
    h = db.execute("SELECT h.*, w.number AS ward_number, w.name AS ward_name FROM households h JOIN wards w ON w.id=h.ward_id WHERE h.id=?",
                   (req["household_id"],)).fetchone()
    m = db.execute("SELECT * FROM household_members WHERE id=?", (req["member_id"],)).fetchone() if req["member_id"] else None
    docs = [dict(d) for d in db.execute(
        "SELECT d.id, d.original_name, d.mime_type, d.size_bytes, d.doc_reference, d.issuing_authority, d.valid_until, d.uploaded_at, rd.round "
        "FROM verification_request_documents rd JOIN documents d ON d.id=rd.document_id WHERE rd.request_id=? ORDER BY rd.round, d.id",
        (request_id,))]
    history = [dict(x) for x in db.execute(
        "SELECT hh.from_status, hh.to_status, hh.actor_type, hh.note, hh.created_at, o.full_name AS official "
        "FROM verification_history hh LEFT JOIN official_users o ON hh.actor_type='official' AND o.id = CAST(hh.actor_id AS INTEGER) "
        "WHERE hh.request_id=? ORDER BY hh.id", (request_id,))]
    reviews = [dict(rv) for rv in db.execute(
        "SELECT rv.*, o.full_name AS official FROM verification_reviews rv JOIN official_users o ON o.id=rv.official_id "
        "WHERE rv.request_id=? ORDER BY rv.id DESC", (request_id,))]
    for rv in reviews:
        rv["checklist"] = json.loads(rv["checklist"]); rv["missing_items"] = json.loads(rv["missing_items"])
    cat = DOCUMENT_CATEGORIES.get(req["document_category"], {})
    can_review = "review_requests" in off["permissions"]
    audit("request_viewed", "verification_request", request_id)
    return jsonify({
        "request": _request_rows("WHERE r.id = ?", [request_id], "")[0] | {"citizen_remarks": req["citizen_remarks"],
                                                                           "official_remarks": req["official_remarks"]},
        "applicant": {"citizen_ref": "CIT-" + req["client_id"][:8].upper(), "name": m["name"] if m else None,
                      "member_ref": m["member_ref"] if m else None, "household_ref": h["household_ref"],
                      "ward": f"{h['ward_number']} - {h['ward_name']}", "request_date": req["submitted_at"]},
        "category": {"code": req["document_category"], "name_en": cat.get("name_en"), "scope": cat.get("scope"),
                     "note_en": cat.get("note_en"), "refer_to": cat.get("refer_to")},
        "checklist": checklist_for(req["document_category"]) if cat else [],
        "documents": docs, "history": history, "reviews": reviews,
        "actions": _allowed_actions(req, can_review),
    })


def _allowed_actions(req, can_review):
    if not can_review:
        return []
    off = g.official
    mine = req["assigned_official_id"] in (None, off["id"]) or off["role"] == "panchayat_admin"
    if req["status"] == "queued_for_review":
        return ["start_review"]
    if req["status"] == "under_review" and mine:
        acts = ["save_draft", "request_correction", "mark_unsuccessful", "refer"]
        if DOCUMENT_CATEGORIES[req["document_category"]]["scope"] == "record_check":
            acts.insert(0, "verify")
        return acts
    return []


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _act(request_id, action):
    """Common guard for review actions: scope + action allowed for this official right now."""
    req = _load_scoped(request_id)
    if not req:
        return None, (jsonify({"error": "Request not found"}), 404)
    if action not in _allowed_actions(req, True):
        return None, (jsonify({"error": f"'{action.replace('_', ' ')}' is not possible while the request is "
                                        f"'{STATUSES[req['status']][0]}' or it is assigned to another official."}), 409)
    return req, None


@bp.post("/verification-requests/<int:request_id>/start-review")
@require_official("review_requests", feature="verification")
def start_review(request_id):
    req, err = _act(request_id, "start_review")
    if err:
        return err
    db = get_db()
    with transaction(db):
        db.execute("UPDATE verification_requests SET assigned_official_id=? WHERE id=?", (g.official["id"], request_id))
        transition(db, req, "under_review", "official", g.official["id"], "Review started")
        notify_citizen(db, req, "under_review")
        audit("review_started", "verification_request", request_id, db=db)
    return jsonify({"status": "under_review"})


@bp.post("/verification-requests/<int:request_id>/review")
@require_official("review_requests", feature="verification")
def save_draft(request_id):
    """Save notes without finishing. A draft never appears as a result to the citizen."""
    req, err = _act(request_id, "save_draft")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    checklist = _clean_checklist(req, body.get("checklist"))
    db = get_db()
    db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, checklist, remarks, internal_notes, created_at) "
               "VALUES (?, ?, 'draft', ?, ?, ?, ?)", (request_id, g.official["id"], dumps(checklist),
                                                      _text(body.get("remarks")), _text(body.get("internal_notes")), now()))
    audit("review_draft_saved", "verification_request", request_id)
    return jsonify({"saved": True, "status": req["status"]})


def _clean_checklist(req, raw):
    ids = [c["id"] for c in checklist_for(req["document_category"])]
    raw = raw if isinstance(raw, dict) else {}
    return {i: raw.get(i) is True for i in ids}


def _text(v, limit=1000):
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


@bp.post("/verification-requests/<int:request_id>/verify")
@require_official("review_requests", feature="verification")
def verify(request_id):
    req, err = _act(request_id, "verify")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify({"error": "Please confirm the verification result before submitting."}), 400
    checklist = _clean_checklist(req, body.get("checklist"))
    missing = [k for k, v in checklist.items() if not v]
    if missing:
        return jsonify({"error": "All verification checks must be completed before marking as verified.", "incomplete_checks": missing}), 400
    remarks = _text(body.get("remarks"))
    db = get_db()
    with transaction(db):
        cur = db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, outcome, checklist, remarks, official_reference, created_at) "
                         "VALUES (?, ?, 'final', 'verified', ?, ?, ?, ?)",
                         (request_id, g.official["id"], dumps(checklist), remarks, _text(body.get("official_reference"), 80), now()))
        receipt = make_ref("VRC", cur.lastrowid)
        db.execute("UPDATE verification_reviews SET receipt_ref=? WHERE id=?", (receipt, cur.lastrowid))
        db.execute("UPDATE verification_requests SET official_remarks=? WHERE id=?", (remarks, request_id))
        transition(db, req, "verified", "official", g.official["id"], "Verified against panchayat records")
        notify_citizen(db, req, "verified")
        audit("verified", "verification_request", request_id, detail={"receipt_ref": receipt}, db=db)
    return jsonify({"status": "verified", "receipt_ref": receipt,
                    "note": "Verification recorded. No scholarship or benefit has been approved by this action."})


@bp.post("/verification-requests/<int:request_id>/request-correction")
@require_official("review_requests", feature="verification")
def request_correction(request_id):
    req, err = _act(request_id, "request_correction")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    items = [i for i in (body.get("missing_items") or []) if i in CORRECTION_REASONS]
    reason = _text(body.get("reason"))
    if not items or not reason:
        return jsonify({"error": "Choose at least one item and explain what the citizen must provide."}), 400
    remark = "; ".join(CORRECTION_REASONS[i] for i in items) + ". " + reason
    db = get_db()
    with transaction(db):
        db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, outcome, checklist, remarks, missing_items, created_at) "
                   "VALUES (?, ?, 'final', 'info_required', ?, ?, ?, ?)",
                   (request_id, g.official["id"], dumps(_clean_checklist(req, body.get("checklist"))), remark, dumps(items), now()))
        db.execute("UPDATE verification_requests SET official_remarks=? WHERE id=?", (remark, request_id))
        transition(db, req, "info_required", "official", g.official["id"], remark)
        notify_citizen(db, req, "info_required")
        audit("correction_requested", "verification_request", request_id, detail={"items": items}, db=db)
    return jsonify({"status": "info_required"})


@bp.post("/verification-requests/<int:request_id>/unsuccessful")
@require_official("review_requests", feature="verification")
def unsuccessful(request_id):
    req, err = _act(request_id, "mark_unsuccessful")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    code, reason = body.get("reason_code"), _text(body.get("reason"))
    if code not in UNSUCCESSFUL_REASONS or not reason or len(reason) < 15:
        return jsonify({"error": "Choose a reason category and give a clear explanation (at least 15 characters)."}), 400
    if body.get("confirm") is not True:
        return jsonify({"error": "Please confirm this decision."}), 400
    remark = f"{UNSUCCESSFUL_REASONS[code]}. {reason}"
    db = get_db()
    with transaction(db):
        db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, outcome, checklist, remarks, created_at) "
                   "VALUES (?, ?, 'final', 'unsuccessful', ?, ?, ?)",
                   (request_id, g.official["id"], dumps(_clean_checklist(req, body.get("checklist"))), remark, now()))
        db.execute("UPDATE verification_requests SET official_remarks=? WHERE id=?", (remark, request_id))
        transition(db, req, "unsuccessful", "official", g.official["id"], remark)
        notify_citizen(db, req, "unsuccessful")
        audit("verification_unsuccessful", "verification_request", request_id, detail={"reason_code": code}, db=db)
    return jsonify({"status": "unsuccessful"})


@bp.post("/verification-requests/<int:request_id>/refer")
@require_official("review_requests", feature="verification")
def refer(request_id):
    req, err = _act(request_id, "refer")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    cat, authority, reason = body.get("referral_category"), _text(body.get("authority"), 200), _text(body.get("reason"))
    if cat not in REFERRAL_CATEGORIES or not authority or not reason:
        return jsonify({"error": "Referral category, authority and reason are all required."}), 400
    remark = f"Referred to {authority}: {reason}"
    db = get_db()
    with transaction(db):
        db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, outcome, remarks, referral_authority, referral_category, created_at) "
                   "VALUES (?, ?, 'final', 'referred', ?, ?, ?, ?)", (request_id, g.official["id"], reason, authority, cat, now()))
        db.execute("UPDATE verification_requests SET official_remarks=? WHERE id=?", (remark, request_id))
        transition(db, req, "referred", "official", g.official["id"], remark)
        notify_citizen(db, req, "referred")
        audit("referred", "verification_request", request_id, detail={"category": cat}, db=db)
    return jsonify({"status": "referred"})


# ---------------------------------------------------------------- AI-assisted review
@bp.get("/verification-requests/<int:request_id>/ai-review")
@require_official("review_requests", feature="verification")
def ai_review(request_id):
    """OCR + rule checks on the latest document. Findings only - never changes the request."""
    req = _load_scoped(request_id)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    db = get_db()
    doc = db.execute("SELECT d.* FROM verification_request_documents rd JOIN documents d ON d.id=rd.document_id "
                     "WHERE rd.request_id=? ORDER BY rd.round DESC, d.id DESC LIMIT 1", (request_id,)).fetchone()
    hh = db.execute("SELECT * FROM households WHERE id=?", (req["household_id"],)).fetchone()
    member = db.execute("SELECT * FROM household_members WHERE id=?", (req["member_id"],)).fetchone() if req["member_id"] else None
    findings = []
    base = {"label": "AI-assisted pre-check", "method": "Tesseract OCR + deterministic rules (no LLM)",
            "disclaimer": "These findings are suggestions for the official. They are not proof of authenticity and do not decide anything."}
    if not doc:
        return jsonify({**base, "findings": [{"level": "review", "text": "No document attached."}]})
    if doc["valid_until"]:
        expired = doc["valid_until"] < date.today().isoformat()
        findings.append({"level": "warning" if expired else "ok",
                         "text": f"Validity date entered by citizen: {doc['valid_until']} ({'expired' if expired else 'not expired'}). Confirm on the document."})
    else:
        findings.append({"level": "review", "text": "No validity date recorded; expiry rule is not configured for this category."})
    if doc["mime_type"] == "application/pdf":
        findings.append({"level": "review", "text": "Automatic reading of PDF files is not available in this prototype. Review manually."})
        audit("ai_review_run", "verification_request", request_id)
        return jsonify({**base, "ocr_available": False, "findings": findings, "extracted": {}})
    data = private_files.read(req["panchayat_id"], doc["storage_name"])
    if not document_service.OCR_AVAILABLE:
        findings.append({"level": "review", "text": "OCR engine is not installed on this server. Review manually."})
        return jsonify({**base, "ocr_available": False, "findings": findings, "extracted": {}})
    text, conf = document_service.run_ocr(data)
    fields = document_service.parse_income_certificate(text) if req["document_category"] == "income_certificate" else {}
    if not text.strip():
        findings.append({"level": "warning", "text": "No readable text found - the image may be unclear. Consider requesting a clearer copy."})
    else:
        findings.append({"level": "ok" if conf >= 60 else "review", "text": f"Text extracted (reading confidence {conf}%)."})
    reference = {"name": member["name"] if member else None, "annual_income": hh["annual_income"]}
    if member and text:
        if req["document_category"] == "income_certificate" and fields.get("name"):
            issues = document_service.consistency_check(fields, reference)
            for i in issues:
                if i["field"] == "name":
                    findings.append({"level": "warning", "text": f"Possible name mismatch: document shows '{i['document_value']}', household record shows '{i['entered_value']}'."})
                elif i["field"] == "annual_income" and i["severity"] == "high":
                    findings.append({"level": "warning", "text": f"Income on document ({i['document_value']:,}) differs from the survey record ({i['entered_value']:,})."})
            if not any(i["field"] == "name" for i in issues):
                findings.append({"level": "ok", "text": "Name on document matches the household record."})
        else:
            present = all(part.lower() in text.lower() for part in member["name"].split()[:2])
            findings.append({"level": "ok" if present else "review",
                             "text": "Applicant's name found in the document text." if present else "Applicant's name was not found in the extracted text - check manually."})
    if req["document_category"] == "income_certificate" and fields.get("annual_income") is None and text.strip():
        findings.append({"level": "review", "text": "Income amount could not be detected."})
    findings.append({"level": "review", "text": "Check for visible alteration or tampering yourself - software cannot confirm this."})
    audit("ai_review_run", "verification_request", request_id)
    return jsonify({**base, "ocr_available": True, "confidence": conf, "findings": findings,
                    "extracted": {k: v for k, v in fields.items() if v is not None}})


# ---------------------------------------------------------------- documents (protected)
@bp.get("/documents/<int:doc_id>/preview")
@require_official("view_documents", feature="verification")
def document_preview(doc_id):
    db = get_db()
    doc = db.execute("SELECT d.*, r.ward_id FROM documents d JOIN verification_request_documents rd ON rd.document_id=d.id "
                     "JOIN verification_requests r ON r.id=rd.request_id WHERE d.id=?", (doc_id,)).fetchone()
    if not doc or not in_scope(doc["panchayat_id"], doc["ward_id"]):
        if doc:
            audit("cross_scope_attempt", "document", doc_id)
        return jsonify({"error": "Document not found"}), 404
    download = request.args.get("download") == "1"
    if download and "download_documents" not in g.official["permissions"]:
        return jsonify({"error": "You do not have permission to download documents."}), 403
    try:
        data = private_files.read(doc["panchayat_id"], doc["storage_name"])
    except FileNotFoundError:
        return jsonify({"error": "The stored file could not be found. Ask the citizen to upload it again."}), 410
    audit("document_downloaded" if download else "document_viewed", "document", doc_id)
    disp = "attachment" if download else "inline"
    return Response(data, mimetype=doc["mime_type"], headers={
        "Content-Disposition": f'{disp}; filename="document-{doc_id}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


@bp.get("/verification-requests/<int:request_id>/receipt")
@require_official("view_requests", feature="verification")
def official_receipt(request_id):
    req = _load_scoped(request_id)
    if not req:
        return jsonify({"error": "Request not found"}), 404
    pdf = build_receipt_pdf(get_db(), req)
    if not pdf:
        return jsonify({"error": "No verification record exists for this request."}), 404
    return Response(pdf, mimetype="application/pdf", headers={"Cache-Control": "no-store",
                    "Content-Disposition": f'attachment; filename="{req["request_ref"]}-record.pdf"'})


def build_receipt_pdf(db, req):
    rv = db.execute("SELECT * FROM verification_reviews WHERE request_id=? AND kind='final' AND outcome='verified' ORDER BY id DESC LIMIT 1",
                    (req["id"],)).fetchone()
    if not rv:
        return None
    p = db.execute("SELECT p.name, p.is_demo, b.name AS block FROM panchayats p JOIN blocks b ON b.id=p.block_id WHERE p.id=?",
                   (req["panchayat_id"],)).fetchone()
    w = db.execute("SELECT number, name FROM wards WHERE id=?", (req["ward_id"],)).fetchone()
    return verification_receipt.build(req, dict(rv), p["name"], p["block"], f"{w['number']} - {w['name']}",
                                      bool(p["is_demo"] or req["is_demo"]))


# ---------------------------------------------------------------- history + notifications
@bp.get("/history")
@require_official("view_requests", feature="verification")
def history():
    rs, rp = scope_sql("r")
    page, size = max(1, _int(request.args.get("page"), 1)), 20
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM verification_history hh JOIN verification_requests r ON r.id=hh.request_id WHERE {rs}", rp).fetchone()[0]
    rows = db.execute(
        f"SELECT hh.*, r.request_ref, r.document_category, o.full_name AS official FROM verification_history hh "
        f"JOIN verification_requests r ON r.id=hh.request_id "
        f"LEFT JOIN official_users o ON hh.actor_type='official' AND o.id = CAST(hh.actor_id AS INTEGER) "
        f"WHERE {rs} ORDER BY hh.id DESC LIMIT {size} OFFSET {(page - 1) * size}", rp).fetchall()
    return jsonify({"items": [{"request_id": r["request_id"], "request_ref": r["request_ref"],
                               "category_en": DOCUMENT_CATEGORIES.get(r["document_category"], {}).get("name_en"),
                               "from": STATUSES[r["from_status"]][0] if r["from_status"] else "-", "to": STATUSES[r["to_status"]][0],
                               "to_status": r["to_status"], "by": r["official"] or r["actor_type"], "note": r["note"], "at": r["created_at"]}
                              for r in rows], "total": total, "page": page, "pages": max(1, -(-total // size))})


@bp.get("/notifications")
@require_official("view_dashboard")
def notifications():
    rows = get_db().execute("SELECT n.* FROM notifications n LEFT JOIN verification_requests r ON r.id=n.request_id "
                            "WHERE n.recipient_type='official' AND n.panchayat_id=? ORDER BY n.id DESC LIMIT 50",
                            (g.official["panchayat_id"],)).fetchall()
    items = []
    for n in rows:
        if n["request_id"]:
            r = get_db().execute("SELECT panchayat_id, ward_id FROM verification_requests WHERE id=?", (n["request_id"],)).fetchone()
            if r and not in_scope(r["panchayat_id"], r["ward_id"]):
                continue
        items.append({"id": n["id"], "title": n["title_en"], "body": n["body_en"], "request_id": n["request_id"],
                      "is_read": bool(n["is_read"]), "created_at": n["created_at"]})
    return jsonify({"items": items, "unread": sum(1 for i in items if not i["is_read"])})


@bp.post("/notifications/read-all")
@require_official("view_dashboard")
def notifications_read():
    get_db().execute("UPDATE notifications SET is_read=1 WHERE recipient_type='official' AND panchayat_id=?",
                     (g.official["panchayat_id"],))
    return jsonify({"ok": True})


@bp.errorhandler(WorkflowError)
def workflow_error(e):
    return jsonify({"error": str(e)}), 409
