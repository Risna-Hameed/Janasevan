"""Development-only FICTIONAL demonstration data for the Panchayat Dashboard.

Every name, household, address and document below is invented. Rows are flagged
is_demo=1 so they can be removed safely with `python manage_demo.py reset`.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone

from flask import current_app
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from services import private_files
from services.panchayat_auth import hash_password
from services.verification_workflow import make_ref

DEMO_PASSWORD = "Demo@2026pass"           # shown on the demo sign-in screen; demo accounts only
DEMO_LINK_CODE_PREFIX = "LINK-"


def ts(days_ago=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)).isoformat(timespec="seconds")


def _font(size, bold=False):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
              "C:/Windows/Fonts/arial%s.ttf" % ("bd" if bold else ""), "/Library/Fonts/Arial.ttf"]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _image_doc(title, lines):
    img = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    d.text((300, 50), title, fill="black", font=_font(40, True))
    d.text((420, 110), "FICTIONAL - FOR DEMO ONLY", fill="red", font=_font(30, True))
    y = 240
    for ln in lines:
        d.text((90, y), ln, fill="black", font=_font(34)); y += 78
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()


def _pdf_doc(title, lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 16); c.drawString(60, 780, title)
    c.setFillColorRGB(0.7, 0.1, 0.1); c.drawString(60, 755, "FICTIONAL - FOR DEMO ONLY"); c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 12)
    for i, ln in enumerate(lines):
        c.drawString(60, 710 - i * 22, ln)
    c.showPage(); c.save()
    return buf.getvalue()


FIRST = ["Anu", "Arjun", "Meera", "Rahul", "Fathima", "Joseph", "Lakshmi", "Nikhil", "Shabna", "Vishnu", "Anjali", "Riyas",
         "Devika", "Akhil", "Nimisha", "Sreejith", "Ayesha", "Thomas", "Gopika", "Faisal"]
LAST = ["Demo Kumar", "Sample Nair", "Testing Varghese", "Example Menon", "Placeholder K", "Mock Thomas", "Fiction P", "Demo Rasheed"]
EDU = ["school", "higher_secondary", "undergraduate", "postgraduate", "completed", "not_enrolled", "diploma"]
EMP = ["employed", "self_employed", "unemployed", "homemaker", "retired", "student"]
HOUSING = ["pucca", "pucca", "semi_pucca", "kutcha", "damaged"]
WATER = ["piped", "well", "public_tap", "none"]
SAN = ["own_toilet", "own_toilet", "shared", "none"]

PANCHAYATS = [
    # code, name, wards, licence_status
    ("THANAL", "Thanal Grama Panchayat (fictional)", ["Puthenpura", "Kadavu", "Mele Angadi", "Thazhe Angadi"], "active"),
    ("NILA", "Nila Grama Panchayat (fictional)", ["Kunnu", "Palam", "Chira"], "active"),
    ("PUZHA", "Puzha Grama Panchayat (fictional)", ["Theeram", "Vayal"], "suspended"),
]
OFFICIALS = [
    # username, full name, role, panchayat code, ward numbers (None = all)
    ("thanal.admin", "Demo Secretary (Thanal)", "panchayat_admin", "THANAL", None),
    ("thanal.officer", "Demo Verification Officer (Thanal)", "verification_officer", "THANAL", None),
    ("thanal.ward2", "Demo Ward-2 Officer (Thanal)", "verification_officer", "THANAL", [2]),
    ("thanal.entry", "Demo Survey Operator (Thanal)", "data_entry", "THANAL", None),
    ("thanal.viewer", "Demo Read-only Official (Thanal)", "read_only", "THANAL", None),
    ("nila.admin", "Demo Secretary (Nila)", "panchayat_admin", "NILA", None),
    ("nila.officer", "Demo Verification Officer (Nila)", "verification_officer", "NILA", None),
    ("puzha.admin", "Demo Secretary (Puzha)", "panchayat_admin", "PUZHA", None),
]


def already_seeded(db):
    return db.execute("SELECT 1 FROM panchayats WHERE is_demo=1 LIMIT 1").fetchone() is not None


def seed(db):
    """Insert all demo data in one transaction. Safe to call only when not already seeded."""
    if already_seeded(db):
        return False
    samples = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "samples")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("INSERT INTO blocks (code, name, district, is_demo, created_at) VALUES ('DEMO-BLOCK', "
                   "'Demo Block Panchayat (fictional)', 'Demo District', 1, ?)", (ts(60),))
        block_id = db.execute("SELECT id FROM blocks WHERE code='DEMO-BLOCK'").fetchone()[0]
        pids, wards = {}, {}
        for code, name, ward_names, lic in PANCHAYATS:
            cur = db.execute("INSERT INTO panchayats (block_id, code, name, is_demo, created_at) VALUES (?, ?, ?, 1, ?)", (block_id, code, name, ts(60)))
            pid = pids[code] = cur.lastrowid
            for i, wn in enumerate(ward_names, 1):
                w = db.execute("INSERT INTO wards (panchayat_id, number, name) VALUES (?, ?, ?)", (pid, i, wn + " (demo)"))
                wards[(code, i)] = w.lastrowid
            db.execute("INSERT INTO panchayat_licences (panchayat_id, status, expires_on, features, updated_at) VALUES (?, ?, ?, ?, ?)",
                       (pid, lic, (datetime.now().date() + timedelta(days=365)).isoformat(),
                        json.dumps(["verification", "surveys", "analytics"]), ts(60)))
        oids = {}
        pw_hash = hash_password(DEMO_PASSWORD)
        for username, full, role, code, ward_nums in OFFICIALS:
            cur = db.execute("INSERT INTO official_users (username, full_name, password_hash, role, panchayat_id, is_demo, created_at) "
                             "VALUES (?, ?, ?, ?, ?, 1, ?)", (username, full, pw_hash, role, pids[code], ts(60)))
            oids[username] = cur.lastrowid
            for wn in (ward_nums or [None]):
                db.execute("INSERT INTO official_assignments (official_id, panchayat_id, ward_id) VALUES (?, ?, ?)",
                           (cur.lastrowid, pids[code], wards[(code, wn)] if wn else None))

        # households + members (deterministic, fictional)
        households, k = {}, 0
        for code, _, ward_names, _ in PANCHAYATS:
            for wn in range(1, len(ward_names) + 1):
                for n in range(1, 4 if code != "PUZHA" else 2):
                    k += 1
                    href = f"HH-{pids[code]:02d}-W{wn}-{n:04d}"
                    income = 120000 if k == 1 else [120000, 85000, 240000, 310000, 60000, 150000][k % 6]
                    cur = db.execute(
                        "INSERT INTO households (household_ref, panchayat_id, ward_id, survey_ref, survey_date, address, member_count, annual_income, "
                        "housing_condition, drinking_water, sanitation, validation_status, citizen_link_code_hash, is_demo, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'validated', ?, 1, ?, ?)",
                        (href, pids[code], wards[(code, wn)], f"SRV-{pids[code]:02d}-W{wn}-{k:05d}", (datetime.now().date() - timedelta(days=40 + k)).isoformat(),
                         f"House No. {10 + k}, {ward_names[wn - 1]} Road, Ward {wn} (fictional)", income,
                         HOUSING[k % 5], WATER[k % 4], SAN[k % 4], hash_password(f"{DEMO_LINK_CODE_PREFIX}{1000 + k}"), ts(40 + k), ts(40 + k)))
                    hid = cur.lastrowid
                    names = []
                    size = 2 + (k % 3)
                    for mi in range(size):
                        name = "Anu Demo Kumar" if (k == 1 and mi == 0) else f"{FIRST[(k * 3 + mi) % len(FIRST)]} {LAST[k % len(LAST)]}"
                        age = [19, 47, 44, 15, 72][mi % 5]
                        emp = "student" if age < 25 else EMP[(k + mi) % len(EMP)]
                        edu = "undergraduate" if age == 19 else ("school" if age == 15 else EDU[(k + mi) % len(EDU)])
                        m = db.execute("INSERT INTO household_members (member_ref, household_id, panchayat_id, name, age, education_status, "
                                       "employment_status, assistance_needs, is_student, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                       (f"{href}-M{mi + 1}", hid, pids[code], name, age, edu, emp,
                                        "Mobility support (demo)" if age > 70 else None, int(emp == "student"), ts(40 + k)))
                        names.append((m.lastrowid, name))
                    db.execute("UPDATE households SET member_count=? WHERE id=?", (size, hid))
                    households[href] = {"id": hid, "pid": pids[code], "wid": wards[(code, wn)], "members": names, "code": code, "ward": wn}

        # survey records in different states
        surveys = [("THANAL", 1, "draft", "House No. 77, Puthenpura Road, Ward 1 (fictional)", None),
                   ("THANAL", 2, "submitted", "House No. 81, Kadavu Road, Ward 2 (fictional)", None),
                   ("THANAL", 3, "needs_correction", "House No. 90, Mele Angadi Road (fictional)", "Member ages missing for two people."),
                   ("THANAL", 4, "submitted", "House No. 14, Thazhe Angadi Road, Ward 4 (fictional)", None),
                   ("NILA", 1, "submitted", "House No. 5, Kunnu Road, Ward 1 (fictional)", None)]
        for i, (code, wn, status, addr, remarks) in enumerate(surveys, 1):
            data = {"address": addr, "annual_income": 90000 + i * 15000, "housing_condition": HOUSING[i % 5], "drinking_water": WATER[i % 4],
                    "sanitation": SAN[i % 4], "survey_date": (datetime.now().date() - timedelta(days=i)).isoformat(),
                    "members": [{"name": f"{FIRST[i]} {LAST[i]}", "age": 40 + i, "education_status": "completed", "employment_status": "self_employed", "is_student": False},
                                {"name": f"{FIRST[i + 5]} {LAST[i]}", "age": 17, "education_status": "higher_secondary", "employment_status": "student", "is_student": True}]}
            cur = db.execute("INSERT INTO survey_records (survey_ref, panchayat_id, ward_id, status, source, original_data, current_data, reviewer_remarks, "
                             "created_by, is_demo, created_at, updated_at) VALUES (?, ?, ?, ?, 'manual', ?, ?, ?, ?, 1, ?, ?)",
                             (f"tmp-{i}", pids[code], wards[(code, wn)], status, json.dumps(data), json.dumps(data), remarks,
                              oids.get(f"{code.lower()}.entry") or oids[f"{code.lower()}.admin"], ts(i), ts(i)))
            db.execute("UPDATE survey_records SET survey_ref=? WHERE id=?", (f"SRV-{pids[code]:02d}-W{wn}-N{cur.lastrowid:04d}", cur.lastrowid))

        # documents + verification requests in every workflow state
        def store(pid, data, name, client, **meta):
            info = private_files.save(pid, name, data)
            cur = db.execute("INSERT INTO documents (panchayat_id, client_id, storage_name, original_name, mime_type, size_bytes, sha256, "
                             "doc_reference, issuing_authority, valid_until, uploaded_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                             (pid, client, info["storage_name"], info["original_name"], info["mime_type"], info["size_bytes"], info["sha256"],
                              meta.get("ref"), meta.get("authority"), meta.get("valid"), ts(meta.get("days", 0))))
            return cur.lastrowid

        with open(os.path.join(samples, "sample_income_certificate.png"), "rb") as fh:
            good_income = fh.read()
        with open(os.path.join(samples, "sample_income_certificate_mismatch.png"), "rb") as fh:
            bad_income = fh.read()

        plan = [
            # household, category, purpose, scheme, final status, days ago, doc kind
            ("HH-01-W1-0001", "income_certificate", "Central Sector Scholarship application", "nsp_central_sector", "queued_for_review", 1, "good_income"),
            ("HH-01-W1-0002", "residence_proof", "AICTE Pragati application", "aicte_pragati", "under_review", 3, "residence"),
            ("HH-01-W2-0001", "income_certificate", "State merit scholarship", None, "queued_for_review", 9, "bad_income"),
            ("HH-01-W2-0002", "educational_certificate", "Post-matric scholarship", "nsp_pms_minorities", "info_required", 5, "edu_pdf"),
            ("HH-01-W3-0001", "residence_proof", "Hostel admission", None, "verified", 12, "residence"),
            ("HH-01-W3-0002", "community_certificate", "Post-matric scholarship", "nsp_pms_minorities", "referred", 15, "community"),
            ("HH-01-W4-0001", "income_certificate", "Fee concession", None, "unsuccessful", 20, "bad_income"),
            ("HH-01-W4-0002", "non_creamy_layer_certificate", "Central Sector Scholarship application", "nsp_central_sector", "queued_for_review", 2, "community"),
            ("HH-02-W1-0001", "income_certificate", "Scholarship (Nila demo)", None, "queued_for_review", 2, "good_income"),
            ("HH-02-W2-0001", "residence_proof", "Internship application (Nila demo)", None, "verified", 8, "residence"),
        ]
        officer = {"THANAL": oids["thanal.officer"], "NILA": oids["nila.officer"]}
        for i, (href, cat, purpose, scheme, final, days, kind) in enumerate(plan, 1):
            h = households[href]
            client = f"demo-citizen-{i:04d}"
            mid, mname = h["members"][0]
            if kind == "good_income":
                data, fname = good_income, "income_certificate.png"
            elif kind == "bad_income":
                data, fname = bad_income, "income_certificate.png"
            elif kind == "edu_pdf":
                data, fname = _pdf_doc("SAMPLE MARK LIST", [f"Name: {mname}", "Examination: Higher Secondary (demo)", "Result: Passed"]), "marklist.pdf"
            elif kind == "community":
                data, fname = _image_doc("SAMPLE CATEGORY CERTIFICATE", [f"Name: {mname}", "Category: (fictional demo value)", "Issued by: Demo Office"]), "certificate.png"
            else:
                data, fname = _image_doc("SAMPLE RESIDENCE CERTIFICATE", [f"This is to certify that {mname}", f"resides at Ward {h['ward']} (fictional)",
                                                                           "Issued on: 01/09/2026"]), "residence.png"
            doc_id = store(h["pid"], data, fname, client, days=days, ref=f"DEMO-DOC-{i:03d}", authority="Demo issuing office",
                           valid=(datetime.now().date() + timedelta(days=200 if i % 3 else -10)).isoformat())
            cur = db.execute("INSERT INTO verification_requests (request_ref, client_id, household_id, member_id, panchayat_id, ward_id, document_category, "
                             "purpose, scheme_id, status, is_demo, submitted_at, updated_at) VALUES ('pending', ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', 1, ?, ?)",
                             (client, h["id"], mid, h["pid"], h["wid"], cat, purpose, scheme, ts(days), ts(days)))
            rid = cur.lastrowid
            ref = make_ref("VER", rid)
            db.execute("UPDATE verification_requests SET request_ref=? WHERE id=?", (ref, rid))
            db.execute("INSERT INTO verification_request_documents (request_id, document_id, round, added_at) VALUES (?, ?, 1, ?)", (rid, doc_id, ts(days)))
            steps = [("submitted", "citizen", client[:8], "Submitted from the citizen portal"), ("queued_for_review", "system", "router", "Routed to panchayat")]
            off = officer[h["code"]]
            review = None
            if final in ("under_review", "info_required", "verified", "referred", "unsuccessful"):
                steps.append(("under_review", "official", off, "Review started"))
            if final == "info_required":
                review = ("info_required", "Document image is unreadable. Please upload a clearer scan of all pages.")
            elif final == "verified":
                review = ("verified", "Details consistent with household survey record.")
            elif final == "referred":
                review = ("referred", "Community status must be certified by the revenue authority.")
            elif final == "unsuccessful":
                review = ("unsuccessful", "Details do not match the panchayat household record. Name and income on the document differ from the survey record.")
            if review:
                steps.append((final, "official", off, review[1]))
            prev = None
            for j, (status, actor, actor_id, note) in enumerate(steps):
                db.execute("INSERT INTO verification_history (request_id, from_status, to_status, actor_type, actor_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (rid, prev, status, actor, str(actor_id), note, ts(days, hours=-j)))
                prev = status
            closed = ts(max(days - 1, 0)) if final in ("verified", "referred", "unsuccessful") else None
            db.execute("UPDATE verification_requests SET status=?, assigned_official_id=?, official_remarks=?, updated_at=?, closed_at=? WHERE id=?",
                       (final, off if final != "queued_for_review" else None, review[1] if review else None, ts(max(days - 1, 0)), closed, rid))
            if review:
                rc = db.execute("INSERT INTO verification_reviews (request_id, official_id, kind, outcome, checklist, remarks, missing_items, referral_authority, "
                                "referral_category, created_at) VALUES (?, ?, 'final', ?, ?, ?, ?, ?, ?, ?)",
                                (rid, off, review[0], json.dumps({"legible": True}), review[1],
                                 json.dumps(["unreadable"] if review[0] == "info_required" else []),
                                 "Demo Village Office (revenue authority)" if review[0] == "referred" else None,
                                 "revenue" if review[0] == "referred" else None, closed or ts(days - 1)))
                if review[0] == "verified":
                    db.execute("UPDATE verification_reviews SET receipt_ref=? WHERE id=?", (make_ref("VRC", rc.lastrowid), rc.lastrowid))
            db.execute("INSERT INTO notifications (recipient_type, panchayat_id, request_id, title_en, title_ml, body_en, body_ml, is_read, created_at) "
                       "VALUES ('official', ?, ?, ?, ?, 'New verification request.', 'പുതിയ രേഖ പരിശോധനാ അപേക്ഷ.', ?, ?)",
                       (h["pid"], rid, ref, ref, int(final != "queued_for_review"), ts(days)))
        db.execute("INSERT INTO audit_logs (panchayat_id, actor_type, actor_id, action, detail, created_at) VALUES (NULL, 'system', 'seed', 'demo_data_seeded', ?, ?)",
                   ("Fictional demonstration data", ts(0)))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return True


DEMO_TABLES_BY_PANCHAYAT = ["notifications", "verification_history", "verification_reviews", "verification_request_documents",
                            "citizen_links", "survey_corrections", "survey_responses"]


def reset(db):
    """Remove ONLY demo rows (demo panchayats and everything inside them). Student tables are untouched."""
    pids = [r[0] for r in db.execute("SELECT id FROM panchayats WHERE is_demo=1")]
    if not pids:
        return 0
    q = ",".join("?" * len(pids))
    storage = [(r["panchayat_id"], r["storage_name"]) for r in db.execute(f"SELECT panchayat_id, storage_name FROM documents WHERE panchayat_id IN ({q})", pids)]
    db.execute("BEGIN IMMEDIATE")
    try:
        rids = f"SELECT id FROM verification_requests WHERE panchayat_id IN ({q})"
        db.execute(f"DELETE FROM notifications WHERE panchayat_id IN ({q})", pids)
        for t in ("verification_history", "verification_reviews", "verification_request_documents"):
            db.execute(f"DELETE FROM {t} WHERE request_id IN ({rids})", pids)
        db.execute(f"DELETE FROM verification_requests WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM documents WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM citizen_links WHERE panchayat_id IN ({q})", pids)
        sids = f"SELECT id FROM survey_records WHERE panchayat_id IN ({q})"
        db.execute(f"DELETE FROM survey_corrections WHERE survey_id IN ({sids})", pids)
        db.execute(f"DELETE FROM survey_responses WHERE survey_id IN ({sids})", pids)
        db.execute(f"DELETE FROM survey_records WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM household_members WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM households WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM official_assignments WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM official_users WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM panchayat_licences WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM audit_logs WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM wards WHERE panchayat_id IN ({q})", pids)
        db.execute(f"DELETE FROM panchayats WHERE id IN ({q})", pids)
        db.execute("DELETE FROM blocks WHERE is_demo=1 AND id NOT IN (SELECT block_id FROM panchayats)")
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    for pid, name in storage:
        try:
            os.remove(os.path.join(current_app.config["PRIVATE_UPLOAD_DIR"], str(pid), name))
        except OSError:
            pass
    return len(pids)
