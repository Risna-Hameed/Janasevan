"""Panchayat Dashboard: household records, survey digitisation, ward analytics and administration."""
import json
import re
import secrets

from flask import Blueprint, g, jsonify, request

from database.db import get_db, now, row_to_dict, transaction
from services.panchayat_auth import ROLE_LABELS, ROLE_PERMISSIONS, audit, hash_password, in_scope, licence_for, require_official, scope_sql
from services.verification_workflow import CLOSED_STATUSES, DOCUMENT_CATEGORIES, dumps

bp = Blueprint("panchayat_records", __name__)

HOUSING = ("pucca", "semi_pucca", "kutcha", "damaged")
WATER = ("piped", "well", "public_tap", "none")
SANITATION = ("own_toilet", "shared", "none")
EDUCATION = ("not_enrolled", "school", "higher_secondary", "diploma", "undergraduate", "postgraduate", "completed")
EMPLOYMENT = ("employed", "self_employed", "unemployed", "student", "homemaker", "retired", "not_applicable")


def _int(v, d=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------- households
@bp.get("/households")
@require_official("view_households")
def list_households():
    a = request.args
    where, params = scope_sql("h")
    if a.get("q"):
        where += " AND (h.household_ref LIKE ? OR h.survey_ref LIKE ?)"
        q = f"%{a['q'].strip()[:40]}%"; params += [q, q]
    if a.get("ward"):
        where += " AND w.number = ?"; params.append(_int(a["ward"]))
    if a.get("status"):
        where += " AND h.validation_status = ?"; params.append(a["status"])
    page, size = max(1, _int(a.get("page"), 1)), 15
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM households h JOIN wards w ON w.id=h.ward_id WHERE {where}", params).fetchone()[0]
    rows = db.execute(f"SELECT h.id, h.household_ref, h.survey_ref, h.survey_date, h.member_count, h.validation_status, h.is_demo, "
                      f"w.number AS ward FROM households h JOIN wards w ON w.id=h.ward_id WHERE {where} "
                      f"ORDER BY w.number, h.household_ref LIMIT {size} OFFSET {(page - 1) * size}", params).fetchall()
    # Summary only: no names, no income, no address in the list
    return jsonify({"items": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // size))})


@bp.get("/households/<int:hid>")
@require_official("view_households")
def household_detail(hid):
    db = get_db()
    h = db.execute("SELECT h.*, w.number AS ward_number, w.name AS ward_name FROM households h JOIN wards w ON w.id=h.ward_id WHERE h.id=?",
                   (hid,)).fetchone()
    if not h or not in_scope(h["panchayat_id"], h["ward_id"]):
        if h:
            audit("cross_scope_attempt", "household", hid)
        return jsonify({"error": "Household not found"}), 404
    want_sensitive = request.args.get("sensitive") == "1"
    can_sensitive = "view_sensitive" in g.official["permissions"]
    if want_sensitive and not can_sensitive:
        audit("permission_denied", "household_sensitive", hid)
        return jsonify({"error": "Your role cannot open sensitive household details."}), 403
    members = [dict(m) for m in db.execute("SELECT * FROM household_members WHERE household_id=? ORDER BY id", (hid,))]
    out = row_to_dict(h)
    out.pop("citizen_link_code_hash", None)
    show = want_sensitive and can_sensitive
    if not show:
        out["annual_income"] = None
        for m in members:
            m["assistance_needs"] = None
    audit("household_sensitive_viewed" if show else "household_viewed", "household", hid)
    requests_ = [dict(r) for r in db.execute("SELECT request_ref, status, document_category, submitted_at FROM verification_requests "
                                              "WHERE household_id=? ORDER BY id DESC", (hid,))]
    return jsonify({"household": out, "members": members, "sensitive_shown": show, "can_view_sensitive": can_sensitive,
                    "verification_requests": requests_})


@bp.patch("/households/<int:hid>")
@require_official("edit_households")
def update_household(hid):
    db = get_db()
    h = db.execute("SELECT * FROM households WHERE id=?", (hid,)).fetchone()
    if not h or not in_scope(h["panchayat_id"], h["ward_id"]):
        return jsonify({"error": "Household not found"}), 404
    body = request.get_json(silent=True) or {}
    allowed = {"address": str, "housing_condition": HOUSING, "drinking_water": WATER, "sanitation": SANITATION,
               "validation_status": ("validated", "needs_correction")}
    changes = {}
    for k, rule in allowed.items():
        if k in body:
            v = body[k]
            if rule is str:
                if not isinstance(v, str) or not v.strip():
                    return jsonify({"error": f"{k} must be text"}), 400
                v = v.strip()[:200]
            elif v not in rule:
                return jsonify({"error": f"{k} must be one of {', '.join(rule)}"}), 400
            changes[k] = v
    if not changes:
        return jsonify({"error": "Nothing to update"}), 400
    with transaction(db):
        db.execute(f"UPDATE households SET {', '.join(f'{k}=?' for k in changes)}, updated_at=? WHERE id=?",
                   list(changes.values()) + [now(), hid])
        audit("household_updated", "household", hid, detail={"fields": sorted(changes)}, db=db)
    return jsonify({"updated": sorted(changes)})


# ---------------------------------------------------------------- surveys
def _validate_survey(data):
    """Explicit schema for survey data (also used for any OCR/AI-proposed values)."""
    errors, clean = {}, {}
    addr = data.get("address")
    if not isinstance(addr, str) or len(addr.strip()) < 5:
        errors["address"] = "Address or location description is required (5+ characters)."
    else:
        clean["address"] = addr.strip()[:200]
    for key, options in (("housing_condition", HOUSING), ("drinking_water", WATER), ("sanitation", SANITATION)):
        v = data.get(key)
        if v not in (None, "") and v not in options:
            errors[key] = f"Must be one of: {', '.join(options)}"
        elif v:
            clean[key] = v
    inc = data.get("annual_income")
    if inc not in (None, ""):
        n = _int(str(inc).replace(",", ""))
        if n is None or n < 0 or n > 100_000_000:
            errors["annual_income"] = "Must be a number."
        else:
            clean["annual_income"] = n
    if data.get("survey_date"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(data["survey_date"])):
            errors["survey_date"] = "Use YYYY-MM-DD."
        else:
            clean["survey_date"] = data["survey_date"]
    members = data.get("members") or []
    if not isinstance(members, list) or len(members) > 30:
        errors["members"] = "Members must be a list (max 30)."
        members = []
    clean_members = []
    for i, m in enumerate(members):
        if not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"].strip():
            errors[f"members[{i}].name"] = "Name is required."
            continue
        age = _int(m.get("age"))
        if m.get("age") not in (None, "") and (age is None or not 0 <= age <= 120):
            errors[f"members[{i}].age"] = "Age must be 0-120."
        edu, emp = m.get("education_status") or None, m.get("employment_status") or None
        if edu and edu not in EDUCATION:
            errors[f"members[{i}].education_status"] = "Invalid value."
        if emp and emp not in EMPLOYMENT:
            errors[f"members[{i}].employment_status"] = "Invalid value."
        clean_members.append({"name": m["name"].strip()[:80], "age": age, "education_status": edu, "employment_status": emp,
                              "is_student": bool(m.get("is_student")) or emp == "student"})
    clean["members"] = clean_members
    return clean, errors


def _norm_addr(a):
    return re.sub(r"[^a-z0-9]", "", (a or "").lower())


def _find_duplicate(db, panchayat_id, ward_id, address, exclude_id=None):
    key = _norm_addr(address)
    for h in db.execute("SELECT household_ref, address FROM households WHERE panchayat_id=? AND ward_id=?", (panchayat_id, ward_id)):
        if _norm_addr(h["address"]) == key:
            return {"type": "household", "ref": h["household_ref"]}
    for s in db.execute("SELECT id, survey_ref, current_data FROM survey_records WHERE panchayat_id=? AND ward_id=? AND status != 'validated'",
                        (panchayat_id, ward_id)):
        if s["id"] != exclude_id and _norm_addr(json.loads(s["current_data"]).get("address")) == key:
            return {"type": "survey", "ref": s["survey_ref"], "id": s["id"]}
    return None


@bp.get("/surveys")
@require_official("view_households", feature="surveys")
def list_surveys():
    where, params = scope_sql("s")
    if request.args.get("status"):
        where += " AND s.status=?"; params.append(request.args["status"])
    rows = get_db().execute(f"SELECT s.*, w.number AS ward, o.full_name AS created_by_name FROM survey_records s "
                            f"JOIN wards w ON w.id=s.ward_id LEFT JOIN official_users o ON o.id=s.created_by "
                            f"WHERE {where} ORDER BY s.updated_at DESC LIMIT 100", params).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["address"] = d["current_data"].get("address")
        d["member_count"] = len(d["current_data"].get("members", []))
        d.pop("original_data"); d.pop("current_data")
        out.append(d)
    return jsonify({"items": out})


@bp.get("/surveys/<int:sid>")
@require_official("view_households", feature="surveys")
def survey_detail(sid):
    db = get_db()
    s = db.execute("SELECT s.*, w.number AS ward FROM survey_records s JOIN wards w ON w.id=s.ward_id WHERE s.id=?", (sid,)).fetchone()
    if not s or not in_scope(s["panchayat_id"], s["ward_id"]):
        return jsonify({"error": "Survey not found"}), 404
    corrections = [dict(c) for c in db.execute("SELECT c.*, o.full_name AS official FROM survey_corrections c "
                                                "LEFT JOIN official_users o ON o.id=c.official_id WHERE survey_id=? ORDER BY c.id", (sid,))]
    return jsonify({"survey": row_to_dict(s), "corrections": corrections})


@bp.post("/surveys")
@require_official("manage_surveys", feature="surveys")
def create_survey():
    body = request.get_json(silent=True) or {}
    db = get_db()
    ward = db.execute("SELECT * FROM wards WHERE panchayat_id=? AND number=?", (g.official["panchayat_id"], _int(body.get("ward")))).fetchone()
    if not ward or not in_scope(ward["panchayat_id"], ward["id"]):
        return jsonify({"error": "Choose a ward inside your jurisdiction."}), 400
    clean, errors = _validate_survey(body.get("data") or {})
    if errors:
        return jsonify({"error": "Please correct the highlighted fields.", "fields": errors}), 400
    dup = _find_duplicate(db, ward["panchayat_id"], ward["id"], clean["address"])
    with transaction(db):
        cur = db.execute("INSERT INTO survey_records (survey_ref, panchayat_id, ward_id, status, source, original_data, current_data, "
                         "duplicate_of, created_by, created_at, updated_at) VALUES (?, ?, ?, 'draft', 'manual', ?, ?, ?, ?, ?, ?)",
                         ("tmp-" + secrets.token_hex(6), ward["panchayat_id"], ward["id"], dumps(clean), dumps(clean),
                          dup["id"] if dup and dup["type"] == "survey" else None, g.official["id"], now(), now()))
        ref = f"SRV-{g.official['panchayat_id']:02d}-W{ward['number']}-{cur.lastrowid:05d}"
        db.execute("UPDATE survey_records SET survey_ref=? WHERE id=?", (ref, cur.lastrowid))
        audit("survey_created", "survey", cur.lastrowid, db=db)
    return jsonify({"id": cur.lastrowid, "survey_ref": ref, "status": "draft", "possible_duplicate": dup}), 201


@bp.patch("/surveys/<int:sid>")
@require_official("manage_surveys", feature="surveys")
def update_survey(sid):
    db = get_db()
    s = db.execute("SELECT * FROM survey_records WHERE id=?", (sid,)).fetchone()
    if not s or not in_scope(s["panchayat_id"], s["ward_id"]):
        return jsonify({"error": "Survey not found"}), 404
    if s["status"] not in ("draft", "needs_correction"):
        return jsonify({"error": "Only draft surveys or surveys returned for correction can be edited."}), 409
    clean, errors = _validate_survey((request.get_json(silent=True) or {}).get("data") or {})
    if errors:
        return jsonify({"error": "Please correct the highlighted fields.", "fields": errors}), 400
    old = json.loads(s["current_data"])
    with transaction(db):
        for k in sorted(set(old) | set(clean)):
            if old.get(k) != clean.get(k):
                db.execute("INSERT INTO survey_corrections (survey_id, field, old_value, new_value, official_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (sid, k, dumps(old.get(k)), dumps(clean.get(k)), g.official["id"], now()))
        db.execute("UPDATE survey_records SET current_data=?, updated_at=? WHERE id=?", (dumps(clean), now(), sid))  # original_data untouched
        audit("survey_corrected", "survey", sid, db=db)
    return jsonify({"updated": True})


@bp.post("/surveys/<int:sid>/submit")
@require_official("manage_surveys", feature="surveys")
def submit_survey(sid):
    db = get_db()
    s = db.execute("SELECT * FROM survey_records WHERE id=?", (sid,)).fetchone()
    if not s or not in_scope(s["panchayat_id"], s["ward_id"]):
        return jsonify({"error": "Survey not found"}), 404
    if s["status"] not in ("draft", "needs_correction"):
        return jsonify({"error": "This survey has already been submitted."}), 409
    db.execute("UPDATE survey_records SET status='submitted', updated_at=? WHERE id=?", (now(), sid))
    audit("survey_submitted", "survey", sid)
    return jsonify({"status": "submitted"})


@bp.post("/surveys/<int:sid>/validate")
@require_official("validate_surveys", feature="surveys")
def validate_survey(sid):
    """approve -> creates the household + members in one transaction; return -> back for correction."""
    db = get_db()
    s = db.execute("SELECT s.*, w.number AS ward_number FROM survey_records s JOIN wards w ON w.id=s.ward_id WHERE s.id=?", (sid,)).fetchone()
    if not s or not in_scope(s["panchayat_id"], s["ward_id"]):
        return jsonify({"error": "Survey not found"}), 404
    if s["status"] != "submitted":
        return jsonify({"error": "Only submitted surveys can be validated."}), 409
    body = request.get_json(silent=True) or {}
    decision, remarks = body.get("decision"), (body.get("remarks") or "").strip()[:500] or None
    if decision == "return":
        if not remarks:
            return jsonify({"error": "Explain what must be corrected."}), 400
        db.execute("UPDATE survey_records SET status='needs_correction', reviewer_remarks=?, updated_at=? WHERE id=?", (remarks, now(), sid))
        audit("survey_returned", "survey", sid)
        return jsonify({"status": "needs_correction"})
    if decision != "approve":
        return jsonify({"error": "decision must be 'approve' or 'return'"}), 400
    data = json.loads(s["current_data"])
    dup = _find_duplicate(db, s["panchayat_id"], s["ward_id"], data["address"], exclude_id=sid)
    if dup and dup["type"] == "household" and body.get("confirm_not_duplicate") is not True:
        return jsonify({"error": f"Possible duplicate of household {dup['ref']}. Confirm it is a different household to continue.",
                        "possible_duplicate": dup}), 409
    with transaction(db):
        code = f"{s['panchayat_id']}{s['ward_number']}{sid:04d}"
        href = f"HH-{s['panchayat_id']:02d}-W{s['ward_number']}-{sid:04d}"
        cur = db.execute("INSERT INTO households (household_ref, panchayat_id, ward_id, survey_ref, survey_date, address, member_count, "
                         "annual_income, housing_condition, drinking_water, sanitation, validation_status, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?)",
                         (href, s["panchayat_id"], s["ward_id"], s["survey_ref"], data.get("survey_date"), data["address"],
                          len(data["members"]), data.get("annual_income"), data.get("housing_condition"), data.get("drinking_water"),
                          data.get("sanitation"), now(), now()))
        for i, m in enumerate(data["members"], 1):
            db.execute("INSERT INTO household_members (member_ref, household_id, panchayat_id, name, age, education_status, employment_status, "
                       "is_student, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (f"{href}-M{i}", cur.lastrowid, s["panchayat_id"], m["name"], m["age"], m["education_status"],
                        m["employment_status"], int(m["is_student"]), now()))
        db.execute("UPDATE survey_records SET status='validated', household_id=?, validated_by=?, reviewer_remarks=?, updated_at=? WHERE id=?",
                   (cur.lastrowid, g.official["id"], remarks, now(), sid))
        audit("survey_validated", "survey", sid, detail={"household_ref": href}, db=db)
    return jsonify({"status": "validated", "household_ref": href})


# ---------------------------------------------------------------- analytics
@bp.get("/analytics")
@require_official("view_analytics", feature="analytics")
def analytics():
    a = request.args
    db = get_db()
    hs, hp = scope_sql("h")
    if a.get("ward"):
        hs += " AND w.number=?"; hp.append(_int(a["ward"]))
    if a.get("from"):
        hs += " AND (h.survey_date IS NULL OR h.survey_date >= ?)"; hp.append(a["from"][:10])
    if a.get("to"):
        hs += " AND (h.survey_date IS NULL OR h.survey_date <= ?)"; hp.append(a["to"][:10])
    j = "FROM households h JOIN wards w ON w.id=h.ward_id"
    one = lambda sql, p: db.execute(sql, p).fetchone()[0]  # noqa: E731
    ss, sp = scope_sql("s")
    if a.get("ward"):
        ss += " AND w.number=?"; sp.append(_int(a["ward"]))
    rs, rp = scope_sql("r")
    if a.get("ward"):
        rs += " AND w.number=?"; rp.append(_int(a["ward"]))
    if a.get("category") in DOCUMENT_CATEGORIES:
        rs += " AND r.document_category=?"; rp.append(a["category"])
    per_ward = [dict(x) for x in db.execute(
        f"SELECT w.number AS ward, COUNT(DISTINCT h.id) AS households, "
        f"(SELECT COUNT(*) FROM household_members m JOIN households h2 ON h2.id=m.household_id WHERE h2.ward_id=w.id) AS individuals, "
        f"(SELECT COUNT(*) FROM verification_requests r2 WHERE r2.ward_id=w.id) AS requests "
        f"FROM wards w LEFT JOIN households h ON h.ward_id=w.id WHERE w.panchayat_id=? GROUP BY w.id ORDER BY w.number",
        (g.official["panchayat_id"],))]
    if g.official["ward_ids"] is not None:
        allowed = {r["number"] for r in db.execute(f"SELECT number FROM wards WHERE id IN ({','.join('?'*len(g.official['ward_ids']))})", g.official["ward_ids"])}
        per_ward = [w for w in per_ward if w["ward"] in allowed]
    turnaround = db.execute(
        f"SELECT AVG(julianday(r.closed_at) - julianday(r.submitted_at)) FROM verification_requests r JOIN wards w ON w.id=r.ward_id "
        f"WHERE {rs} AND r.status IN ({','.join('?'*len(CLOSED_STATUSES))}) AND r.closed_at IS NOT NULL", rp + list(CLOSED_STATUSES)).fetchone()[0]
    return jsonify({
        "survey_responses": {
            "households_surveyed": one(f"SELECT COUNT(*) {j} WHERE {hs}", hp),
            "individuals_covered": one(f"SELECT COUNT(*) FROM household_members m JOIN households h ON h.id=m.household_id JOIN wards w ON w.id=h.ward_id WHERE {hs}", hp),
            "students_recorded": one(f"SELECT COUNT(*) FROM household_members m JOIN households h ON h.id=m.household_id JOIN wards w ON w.id=h.ward_id WHERE {hs} AND m.is_student=1", hp),
            "households_reporting_unemployment": one(f"SELECT COUNT(DISTINCT h.id) FROM household_members m JOIN households h ON h.id=m.household_id JOIN wards w ON w.id=h.ward_id WHERE {hs} AND m.employment_status='unemployed'", hp),
        },
        "preliminary_flags": {
            "_note": "Preliminary analytical flags from survey answers only - not official classifications.",
            "housing_assessment_suggested": one(f"SELECT COUNT(*) {j} WHERE {hs} AND h.housing_condition IN ('kutcha','damaged')", hp),
            "infrastructure_needs_reported": one(f"SELECT COUNT(*) {j} WHERE {hs} AND (h.drinking_water='none' OR h.sanitation='none')", hp),
        },
        "data_quality": {
            "incomplete_survey_records": one(f"SELECT COUNT(*) FROM survey_records s JOIN wards w ON w.id=s.ward_id WHERE {ss} AND s.status IN ('draft','needs_correction')", sp),
            "records_awaiting_validation": one(f"SELECT COUNT(*) FROM survey_records s JOIN wards w ON w.id=s.ward_id WHERE {ss} AND s.status='submitted'", sp),
        },
        "verification": {
            "by_ward": {f"Ward {w['ward']}": w["requests"] for w in per_ward},
            "average_turnaround_days": round(turnaround, 1) if turnaround is not None else None,
        },
        "per_ward": per_ward,
        "note": "Aggregates only. No individual household is identifiable on this page.",
    })


# ---------------------------------------------------------------- administration
@bp.get("/admin/officials")
@require_official("manage_users")
def list_officials():
    db = get_db()
    rows = db.execute("SELECT id, username, full_name, role, is_active, is_demo, last_login_at FROM official_users WHERE panchayat_id=? ORDER BY role, username",
                      (g.official["panchayat_id"],)).fetchall()
    out = []
    for r in rows:
        wards = [w["number"] for w in db.execute("SELECT w.number FROM official_assignments a JOIN wards w ON w.id=a.ward_id WHERE a.official_id=?", (r["id"],))]
        out.append(dict(r) | {"role_label": ROLE_LABELS[r["role"]], "wards": wards or "all"})
    return jsonify({"items": out, "roles": ROLE_LABELS, "permissions": {k: sorted(v) for k, v in ROLE_PERMISSIONS.items()}})


@bp.post("/admin/officials")
@require_official("manage_users")
def create_official():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip().lower()
    name, role, pw = (body.get("full_name") or "").strip(), body.get("role"), body.get("password") or ""
    if not re.match(r"^[a-z0-9._-]{4,32}$", username):
        return jsonify({"error": "Username: 4-32 lowercase letters, numbers, dot, dash or underscore."}), 400
    if role not in ROLE_PERMISSIONS or not name:
        return jsonify({"error": "Full name and a valid role are required."}), 400
    if len(pw) < 10 or not re.search(r"\d", pw) or not re.search(r"[A-Za-z]", pw):
        return jsonify({"error": "Password must be at least 10 characters with letters and numbers."}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM official_users WHERE username=?", (username,)).fetchone():
        return jsonify({"error": "That username is taken."}), 409
    ward_ids = _ward_ids(db, body.get("wards"))
    if ward_ids is False:
        return jsonify({"error": "Wards must belong to your panchayat."}), 400
    with transaction(db):
        cur = db.execute("INSERT INTO official_users (username, full_name, password_hash, role, panchayat_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (username, name[:80], hash_password(pw), role, g.official["panchayat_id"], now()))
        _set_assignments(db, cur.lastrowid, ward_ids)
        audit("official_created", "official_user", cur.lastrowid, detail={"role": role}, db=db)   # never the password
    return jsonify({"id": cur.lastrowid}), 201


def _ward_ids(db, numbers):
    if not numbers:
        return []
    ids = []
    for n in numbers:
        w = db.execute("SELECT id FROM wards WHERE panchayat_id=? AND number=?", (g.official["panchayat_id"], _int(n))).fetchone()
        if not w:
            return False
        ids.append(w["id"])
    return ids


def _set_assignments(db, oid, ward_ids):
    db.execute("DELETE FROM official_assignments WHERE official_id=?", (oid,))
    for wid in (ward_ids or [None]):
        db.execute("INSERT INTO official_assignments (official_id, panchayat_id, ward_id) VALUES (?, ?, ?)", (oid, g.official["panchayat_id"], wid))


@bp.post("/admin/officials/<int:oid>/assignments")
@require_official("manage_users")
def set_assignments(oid):
    db = get_db()
    o = db.execute("SELECT * FROM official_users WHERE id=? AND panchayat_id=?", (oid, g.official["panchayat_id"])).fetchone()
    if not o:
        return jsonify({"error": "Official not found"}), 404
    ward_ids = _ward_ids(db, (request.get_json(silent=True) or {}).get("wards"))
    if ward_ids is False:
        return jsonify({"error": "Wards must belong to your panchayat."}), 400
    with transaction(db):
        _set_assignments(db, oid, ward_ids)
        audit("assignments_changed", "official_user", oid, db=db)
    return jsonify({"ok": True})


@bp.post("/admin/officials/<int:oid>/active")
@require_official("manage_users")
def set_active(oid):
    if oid == g.official["id"]:
        return jsonify({"error": "You cannot deactivate your own account."}), 400
    db = get_db()
    active = bool((request.get_json(silent=True) or {}).get("active"))
    cur = db.execute("UPDATE official_users SET is_active=? WHERE id=? AND panchayat_id=?", (int(active), oid, g.official["panchayat_id"]))
    if cur.rowcount == 0:
        return jsonify({"error": "Official not found"}), 404
    audit("official_activated" if active else "official_deactivated", "official_user", oid)
    return jsonify({"active": active})


@bp.get("/admin/licence")
@require_official("manage_users")
def licence():
    return jsonify(licence_for(g.official["panchayat_id"]) | {
        "note": "Licences are managed by the platform operator (see README: manage_demo.py). A licence never replaces sign-in."})


@bp.get("/admin/audit")
@require_official("view_audit")
def audit_log():
    rows = get_db().execute("SELECT a.*, o.full_name FROM audit_logs a LEFT JOIN official_users o ON a.actor_type='official' "
                            "AND o.id = CAST(a.actor_id AS INTEGER) WHERE a.panchayat_id=? ORDER BY a.id DESC LIMIT 100",
                            (g.official["panchayat_id"],)).fetchall()
    return jsonify({"items": [{"action": r["action"], "actor": r["full_name"] or r["actor_type"], "target": f"{r['target_type'] or ''} {r['target_id'] or ''}".strip(),
                               "at": r["created_at"], "ip": r["ip"]} for r in rows]})
