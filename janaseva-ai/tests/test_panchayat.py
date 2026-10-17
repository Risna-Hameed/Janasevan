"""Panchayat Dashboard tests. Uses a throw-away database and file folder - never the real one.

Run:  python tests/test_panchayat.py      (or: python -m pytest tests/test_panchayat.py)
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_API_KEY"] = ""

from config import Config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="janaseva_test_")


class TestConfig(Config):
    DATABASE = os.path.join(TMP, "test.db")
    PRIVATE_UPLOAD_DIR = os.path.join(TMP, "private")
    SEED_DEMO = True
    TESTING = True


from app import create_app  # noqa: E402

app = create_app(TestConfig)
PW = "Demo@2026pass"
SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "samples")


def official(username):
    c = app.test_client()
    r = c.post("/api/panchayat/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.get_json()
    return c, {"X-CSRF-Token": r.get_json()["csrf_token"]}


def citizen(cid="test-citizen-0001", household="HH-01-W1-0001", code="LINK-1001"):
    c = app.test_client()
    h = {"X-Client-Id": cid}
    r = c.post("/api/citizen/link", json={"household_ref": household, "link_code": code}, headers=h)
    assert r.status_code == 200, r.get_json()
    return c, h


def submit(c, h, category="income_certificate", fname="sample_income_certificate.png", member=True):
    with open(os.path.join(SAMPLES, fname), "rb") as fh:
        data = {"file": (io.BytesIO(fh.read()), "cert.png"), "document_category": category,
                "purpose": "Central Sector Scholarship", "scheme_id": "nsp_central_sector"}
    if member:
        data["member_ref"] = "HH-01-W1-0001-M1"
    r = c.post("/api/citizen/verification-requests", data=data, headers=h, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def find_id(oc, ref):
    items = oc.get(f"/api/panchayat/verification-requests?q={ref}").get_json()["items"]
    return items[0]["id"]


def full_checklist(oc, rid):
    return {c["id"]: True for c in oc.get(f"/api/panchayat/verification-requests/{rid}").get_json()["checklist"]}


# ------------------------------------------------------------------ 1-4 dashboard, listing, filters
def test_dashboard_page_and_login_required():
    c = app.test_client()
    assert c.get("/panchayat").status_code == 200
    assert c.get("/api/panchayat/dashboard").status_code == 401                 # 14 unauthorised
    assert c.post("/api/panchayat/login", json={"username": "thanal.admin", "password": "wrong"}).status_code == 401


def test_dashboard_statistics_come_from_database():
    oc, _ = official("thanal.admin")
    s = oc.get("/api/panchayat/dashboard").get_json()["stats"]
    with sqlite3.connect(TestConfig.DATABASE) as db:
        pid = db.execute("SELECT id FROM panchayats WHERE code='THANAL'").fetchone()[0]
        hh = db.execute("SELECT COUNT(*) FROM households WHERE panchayat_id=?", (pid,)).fetchone()[0]
        ver = db.execute("SELECT COUNT(*) FROM verification_requests WHERE panchayat_id=? AND status='verified'", (pid,)).fetchone()[0]
    assert s["households"] == hh and s["verified"] == ver and s["wards"] == 4


def test_request_listing_filtering_and_pagination():
    oc, _ = official("thanal.officer")
    all_ = oc.get("/api/panchayat/verification-requests?status=&page_size=50").get_json()
    assert all_["total"] >= 8 and all(i["request_ref"].startswith("JSA-VER-") for i in all_["items"])
    inc = oc.get("/api/panchayat/verification-requests?status=&category=income_certificate").get_json()
    assert inc["items"] and all(i["document_category"] == "income_certificate" for i in inc["items"])
    w2 = oc.get("/api/panchayat/verification-requests?status=&ward=2").get_json()
    assert all(i["ward"] == 2 for i in w2["items"])
    p = oc.get("/api/panchayat/verification-requests?status=&page_size=5&page=2").get_json()
    assert p["page"] == 2 and len(p["items"]) <= 5


# ------------------------------------------------------------------ 5, 10, 16, 17 full citizen -> panchayat -> citizen flow
def test_end_to_end_verification_with_notification_and_receipt():
    cc, ch = citizen()
    created = submit(cc, ch)
    assert created["status"] == "queued_for_review" and created["request_ref"].startswith("JSA-VER-2026-")
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    assert oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh).status_code == 200
    ai = oc.get(f"/api/panchayat/verification-requests/{rid}/ai-review").get_json()
    assert ai["findings"] and "not proof" in ai["disclaimer"]
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/verify",
                json={"confirm": True, "checklist": full_checklist(oc, rid), "remarks": "Matches survey record."}, headers=oh)
    assert r.status_code == 200 and r.get_json()["receipt_ref"].startswith("JSA-VRC-")
    mine = cc.get("/api/citizen/verification-requests", headers=ch).get_json()["items"][0]
    assert mine["status"] == "verified" and mine["receipt_available"]
    notes = cc.get("/api/citizen/notifications", headers=ch).get_json()["items"]
    assert any("Verification completed" in n["title_en"] for n in notes)
    assert all("120000" not in n["body_en"] and "income" not in n["body_en"].lower() for n in notes)  # no sensitive data
    pdf = cc.get(f"/api/citizen/verification-requests/{mine['id']}/receipt", headers=ch)
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    with sqlite3.connect(TestConfig.DATABASE) as db:                              # persisted
        assert db.execute("SELECT status FROM verification_requests WHERE id=?", (rid,)).fetchone()[0] == "verified"
        assert db.execute("SELECT COUNT(*) FROM verification_history WHERE request_id=?", (rid,)).fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='verified' AND target_id=?", (str(rid),)).fetchone()[0] == 1


def test_verification_does_not_approve_scholarship():
    """Verification must not touch the citizen's application drafts or any benefit status."""
    cc, ch = citizen("test-citizen-0007")
    prof = {"state": "Kerala", "education_level": "UG", "annual_income": 120000}
    d = cc.post("/api/application/draft", json={"scheme_id": "nsp_central_sector", "profile": prof, "provided_items": []}, headers=ch).get_json()
    created = submit(cc, ch)
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/verify", json={"confirm": True, "checklist": full_checklist(oc, rid)}, headers=oh)
    assert "No scholarship" in r.get_json()["note"]
    assert cc.get(f"/api/application/{d['draft_id']}", headers=ch).get_json()["status"] == "draft"


# ------------------------------------------------------------------ 6, 7 correction + resubmission, referral
def test_correction_request_and_resubmission_keeps_history():
    cc, ch = citizen("test-citizen-0002")
    created = submit(cc, ch)
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    bad = oc.post(f"/api/panchayat/verification-requests/{rid}/request-correction", json={"missing_items": []}, headers=oh)
    assert bad.status_code == 400                                                   # 9 missing fields
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/request-correction",
                json={"missing_items": ["unreadable"], "reason": "Please upload a clearer scan."}, headers=oh)
    assert r.status_code == 200
    mine = cc.get(f"/api/citizen/verification-requests/{created['id']}", headers=ch).get_json()
    assert mine["status"] == "info_required" and "clearer scan" in mine["official_remarks"] and mine["can_resubmit"]
    with open(os.path.join(SAMPLES, "sample_income_certificate.png"), "rb") as fh:
        rs = cc.post(f"/api/citizen/verification-requests/{created['id']}/resubmit", data={"file": (io.BytesIO(fh.read()), "new.png")},
                     headers=ch, content_type="multipart/form-data")
    assert rs.status_code == 200 and rs.get_json()["status"] == "queued_for_review"
    docs = oc.get(f"/api/panchayat/verification-requests/{rid}").get_json()["documents"]
    assert [d["round"] for d in docs] == [1, 2]                                      # original kept


def test_referral_and_refer_only_category():
    cc, ch = citizen("test-citizen-0003")
    created = submit(cc, ch, category="community_certificate")
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    assert "verify" not in oc.get(f"/api/panchayat/verification-requests/{rid}").get_json()["actions"]
    v = oc.post(f"/api/panchayat/verification-requests/{rid}/verify", json={"confirm": True, "checklist": {"legible": True}}, headers=oh)
    assert v.status_code == 409                                                     # panchayat cannot verify this type
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/refer",
                json={"referral_category": "revenue", "authority": "Demo Village Office", "reason": "Community status is certified by revenue."}, headers=oh)
    assert r.status_code == 200
    mine = cc.get(f"/api/citizen/verification-requests/{created['id']}", headers=ch).get_json()
    assert mine["status"] == "referred" and mine["referral_authority"] == "Demo Village Office"


# ------------------------------------------------------------------ 8 invalid transitions, 9 missing fields
def test_invalid_transitions_rejected():
    oc, oh = official("thanal.officer")
    closed = oc.get("/api/panchayat/verification-requests?status=verified").get_json()["items"][0]["id"]
    assert oc.post(f"/api/panchayat/verification-requests/{closed}/start-review", headers=oh).status_code == 409
    queued = oc.get("/api/panchayat/verification-requests?status=queued_for_review").get_json()["items"][0]["id"]
    assert oc.post(f"/api/panchayat/verification-requests/{queued}/verify",
                   json={"confirm": True, "checklist": full_checklist(oc, queued)}, headers=oh).status_code == 409  # must start review first
    cc, ch = citizen("test-citizen-0004")
    created = submit(cc, ch)
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    assert cc.post(f"/api/citizen/verification-requests/{created['id']}/cancel", headers=ch).status_code == 409   # under review: no cancel
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/verify", json={"checklist": full_checklist(oc, rid)}, headers=oh)
    assert r.status_code == 400                                                     # confirmation missing
    r = oc.post(f"/api/panchayat/verification-requests/{rid}/unsuccessful", json={"reason_code": "record_mismatch", "reason": "no", "confirm": True}, headers=oh)
    assert r.status_code == 400                                                     # vague reason refused


def test_assigned_to_other_officer_cannot_act():
    cc, ch = citizen("test-citizen-0005")
    created = submit(cc, ch)
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    # thanal.ward2 only covers ward 2; this request is in ward 1 -> not even visible
    o2, h2 = official("thanal.ward2")
    assert o2.get(f"/api/panchayat/verification-requests/{rid}").status_code == 404


# ------------------------------------------------------------------ 11 households, 12 surveys
def test_household_summary_and_sensitive_access():
    oc, _ = official("thanal.viewer")
    items = oc.get("/api/panchayat/households").get_json()["items"]
    assert items and "annual_income" not in items[0]
    hid = items[0]["id"]
    d = oc.get(f"/api/panchayat/households/{hid}").get_json()
    assert d["household"]["annual_income"] is None and not d["can_view_sensitive"]
    assert oc.get(f"/api/panchayat/households/{hid}?sensitive=1").status_code == 403
    ad, _ = official("thanal.admin")
    assert ad.get(f"/api/panchayat/households/{hid}?sensitive=1").get_json()["household"]["annual_income"] is not None


def test_survey_create_correct_submit_validate():
    ec, eh = official("thanal.entry")
    data = {"address": "House No. 999, Test Lane, Ward 1 (fictional)", "annual_income": "75,000", "housing_condition": "kutcha",
            "members": [{"name": "Test Person", "age": 30, "employment_status": "unemployed"}]}
    bad = ec.post("/api/panchayat/surveys", json={"ward": 1, "data": {"address": "x"}}, headers=eh)
    assert bad.status_code == 400 and "address" in bad.get_json()["fields"]
    r = ec.post("/api/panchayat/surveys", json={"ward": 1, "data": data}, headers=eh)
    assert r.status_code == 201
    sid = r.get_json()["id"]
    data2 = dict(data, annual_income=80000)
    assert ec.patch(f"/api/panchayat/surveys/{sid}", json={"data": data2}, headers=eh).status_code == 200
    det = ec.get(f"/api/panchayat/surveys/{sid}").get_json()
    assert det["survey"]["original_data"]["annual_income"] == 75000 and det["corrections"]        # original preserved
    assert ec.post(f"/api/panchayat/surveys/{sid}/validate", json={"decision": "approve"}, headers=eh).status_code == 403  # entry cannot validate
    assert ec.post(f"/api/panchayat/surveys/{sid}/submit", headers=eh).status_code == 200
    ad, ah = official("thanal.admin")
    v = ad.post(f"/api/panchayat/surveys/{sid}/validate", json={"decision": "approve"}, headers=ah).get_json()
    assert v["status"] == "validated" and v["household_ref"].startswith("HH-")
    dup = ec.post("/api/panchayat/surveys", json={"ward": 1, "data": data}, headers=eh).get_json()
    assert dup["possible_duplicate"]["type"] == "household"                                       # duplicate detection


def test_analytics_are_aggregates():
    oc, _ = official("thanal.officer")
    a = oc.get("/api/panchayat/analytics").get_json()
    assert a["survey_responses"]["households_surveyed"] > 0 and "not official" in a["preliminary_flags"]["_note"]
    assert "name" not in str(a["per_ward"])


# ------------------------------------------------------------------ 13-15 tenant isolation, unauthorised, documents
def test_tenant_isolation_households_requests_documents():
    nc, nh = official("nila.officer")
    with sqlite3.connect(TestConfig.DATABASE) as db:
        thanal = db.execute("SELECT id FROM panchayats WHERE code='THANAL'").fetchone()[0]
        hid = db.execute("SELECT id FROM households WHERE panchayat_id=? LIMIT 1", (thanal,)).fetchone()[0]
        rid = db.execute("SELECT id FROM verification_requests WHERE panchayat_id=? LIMIT 1", (thanal,)).fetchone()[0]
        did = db.execute("SELECT id FROM documents WHERE panchayat_id=? LIMIT 1", (thanal,)).fetchone()[0]
    assert nc.get(f"/api/panchayat/households/{hid}").status_code == 404
    assert nc.get(f"/api/panchayat/verification-requests/{rid}").status_code == 404
    assert nc.get(f"/api/panchayat/documents/{did}/preview").status_code == 404
    assert nc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=nh).status_code == 404
    listed = nc.get("/api/panchayat/verification-requests?status=&page_size=50").get_json()["items"]
    assert all(not i["household_ref"].startswith("HH-01-") for i in listed)
    # body/query tampering does not widen scope
    assert nc.get("/api/panchayat/households?panchayat_id=1&q=HH-01").get_json()["total"] == 0


def test_unauthorised_actions():
    anon = app.test_client()
    assert anon.post("/api/panchayat/verification-requests/1/verify", json={"confirm": True}).status_code == 401
    # a citizen (no official session) cannot mark their own document verified
    cc, ch = citizen("test-citizen-0006")
    created = submit(cc, ch)
    assert cc.post(f"/api/panchayat/verification-requests/{created['id']}/verify", json={"confirm": True}, headers=ch).status_code == 401
    # read-only official cannot act; data-entry cannot see requests
    vc, vh = official("thanal.viewer")
    assert vc.post(f"/api/panchayat/verification-requests/{created['id']}/start-review", headers=vh).status_code == 403
    ec, _ = official("thanal.entry")
    assert ec.get("/api/panchayat/verification-requests").status_code == 403
    # CSRF token required for writes
    oc, _ = official("thanal.officer")
    assert oc.post(f"/api/panchayat/verification-requests/{created['id']}/start-review").status_code == 403
    # suspended licence blocks sign-in even with the right password
    assert app.test_client().post("/api/panchayat/login", json={"username": "puzha.admin", "password": PW}).status_code == 403
    # citizens cannot read other citizens' requests
    assert app.test_client().get(f"/api/citizen/verification-requests/{created['id']}", headers={"X-Client-Id": "someone-else-1234"}).status_code == 404


def test_protected_document_retrieval():
    oc, _ = official("thanal.officer")
    rid = oc.get("/api/panchayat/verification-requests?status=").get_json()["items"][0]["id"]
    did = oc.get(f"/api/panchayat/verification-requests/{rid}").get_json()["documents"][0]["id"]
    r = oc.get(f"/api/panchayat/documents/{did}/preview")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert app.test_client().get(f"/api/panchayat/documents/{did}/preview").status_code == 401
    vc, _ = official("thanal.viewer")
    assert vc.get(f"/api/panchayat/documents/{did}/preview").status_code == 403
    with sqlite3.connect(TestConfig.DATABASE) as db:
        name, pid = db.execute("SELECT storage_name, panchayat_id FROM documents WHERE id=?", (did,)).fetchone()
    assert app.test_client().get(f"/static/{name}").status_code == 404
    for path in (f"/static/../instance/private_documents/{pid}/{name}", f"/instance/private_documents/{pid}/{name}", f"/private/{pid}/{name}"):
        r = app.test_client().get(path)
        assert r.mimetype == "text/html" and not r.data.startswith((b"\x89PNG", b"%PDF", b"\xff\xd8"))   # never the file


def test_upload_validation_and_jurisdiction_from_record():
    cc, ch = citizen("test-citizen-0008")
    fake = cc.post("/api/citizen/verification-requests", data={"file": (io.BytesIO(b"MZ not a pdf"), "x.pdf"),
                   "document_category": "residence_proof", "purpose": "Test"}, headers=ch, content_type="multipart/form-data")
    assert fake.status_code == 400
    created = submit(cc, ch)
    with sqlite3.connect(TestConfig.DATABASE) as db:
        pid = db.execute("SELECT panchayat_id FROM verification_requests WHERE id=?", (created["id"],)).fetchone()[0]
        thanal = db.execute("SELECT id FROM panchayats WHERE code='THANAL'").fetchone()[0]
    assert pid == thanal
    unlinked = app.test_client().post("/api/citizen/verification-requests", data={"document_category": "residence_proof", "purpose": "x"},
                                      headers={"X-Client-Id": "never-linked-0001"}, content_type="multipart/form-data")
    assert unlinked.status_code == 403
    assert app.test_client().post("/api/citizen/link", json={"household_ref": "HH-01-W1-0001", "link_code": "WRONG"},
                                  headers={"X-Client-Id": "guesser-000001"}).status_code == 400


def test_ai_review_does_not_change_status():
    """AI findings are advice only - they never move a request or bypass the checklist."""
    cc, ch = citizen("test-citizen-0009")
    created = submit(cc, ch, fname="sample_income_certificate_mismatch.png")
    oc, oh = official("thanal.officer")
    rid = find_id(oc, created["request_ref"])
    oc.post(f"/api/panchayat/verification-requests/{rid}/start-review", headers=oh)
    ai = oc.get(f"/api/panchayat/verification-requests/{rid}/ai-review").get_json()
    assert any(f["level"] == "warning" for f in ai["findings"])
    assert oc.get(f"/api/panchayat/verification-requests/{rid}").get_json()["request"]["status"] == "under_review"
    assert oc.post(f"/api/panchayat/verification-requests/{rid}/verify", json={"confirm": True, "checklist": {}}, headers=oh).status_code == 400


def test_admin_user_management_and_password_hashing():
    ad, ah = official("thanal.admin")
    weak = ad.post("/api/panchayat/admin/officials", json={"username": "new.user", "full_name": "N", "role": "read_only", "password": "short"}, headers=ah)
    assert weak.status_code == 400
    r = ad.post("/api/panchayat/admin/officials", json={"username": "new.user", "full_name": "New User", "role": "read_only",
                                                        "password": "LongEnough123", "wards": [3]}, headers=ah)
    assert r.status_code == 201
    with sqlite3.connect(TestConfig.DATABASE) as db:
        h = db.execute("SELECT password_hash FROM official_users WHERE username='new.user'").fetchone()[0]
        audit_text = " ".join(str(x) for x in db.execute("SELECT detail FROM audit_logs").fetchall())
    assert h != "LongEnough123" and h.startswith(("scrypt:", "pbkdf2:")) and "LongEnough123" not in audit_text
    nc, _ = official("nila.admin")
    assert all(u["username"].startswith("nila") for u in nc.get("/api/panchayat/admin/officials").get_json()["items"])


# ------------------------------------------------------------------ 17, 18 existing features still work; migrations
def test_existing_student_features_still_work():
    c = app.test_client()
    h = {"X-Client-Id": "student-regression-01"}
    assert c.get("/").status_code == 200
    assert len(c.get("/api/schemes").get_json()["schemes"]) >= 5
    p = {"state": "Kerala", "education_level": "UG", "annual_income": 120000, "course_mode": "regular", "class12_percentile": 92, "receives_other_scholarship": False}
    assert c.post("/api/eligibility/check", json={"profile": p, "scheme_id": "nsp_central_sector"}).get_json()["status"] == "potentially_eligible"
    assert c.post("/api/recommend/best", json={"profile": p, "provided_docs": []}).get_json()["results"]
    assert c.post("/api/profile", json={"profile": p}, headers=h).status_code == 200
    assert c.post("/api/chat", json={"message": "hello"}).status_code == 200


def test_migrations_upgrade_old_database_without_data_loss():
    from database.db_schema_student import STUDENT_SCHEMA
    path = os.path.join(TMP, "old.db")
    with sqlite3.connect(path) as db:                                   # a database from before this feature
        db.executescript(STUDENT_SCHEMA)
        db.execute("INSERT INTO citizen_profiles VALUES ('old-client-0001', '{\"state\": \"Kerala\"}', 'en', 'x', 'x')")

    class Old(TestConfig):
        DATABASE = path
        SEED_DEMO = False
    create_app(Old)
    create_app(Old)                                                      # second start: nothing re-applied
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT profile FROM citizen_profiles WHERE client_id='old-client-0001'").fetchone()[0] == '{"state": "Kerala"}'
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3]


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        try:
            fn(); print("PASS", name)
        except Exception as e:  # noqa: BLE001
            failed += 1; print("FAIL", name, type(e).__name__, e)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
