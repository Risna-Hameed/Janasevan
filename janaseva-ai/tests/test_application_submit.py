"""Application submit flow: check -> preview -> change request -> submit -> final PDF.
Uses a temporary database.  Run: python tests/test_application_submit.py"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="janaseva_submit_")


class T(Config):
    DATABASE = os.path.join(TMP, "t.db")
    PRIVATE_UPLOAD_DIR = os.path.join(TMP, "private")
    SEED_DEMO = False


from app import create_app  # noqa: E402

c = create_app(T).test_client()
FULL = {"name": "Anu Demo Kumar", "state": "Kerala", "district": "Malappuram", "education_level": "UG", "course": "B.Tech CSE",
        "institution_name": "Demo College", "annual_income": 120000}
ALL_DOCS = ["income_cert", "marksheet", "institution_cert"]


def draft(cid, profile, docs, draft_id=None, note=None, edits=None):
    r = c.post("/api/application/draft", json={"scheme_id": "test_ug_merit", "profile": profile, "provided_items": docs,
                                               "draft_id": draft_id, "change_note": note, "edits": edits or {}},
               headers={"X-Client-Id": cid})
    return r


def check(cid, did, profile):
    return c.post(f"/api/application/{did}/check", json={"profile": profile}, headers={"X-Client-Id": cid}).get_json()


def test_missing_field_and_document_are_named():
    cid = "sub-test-0001"
    p = {k: v for k, v in FULL.items() if k != "district"}
    did = draft(cid, p, ["income_cert"]).get_json()["draft_id"]
    r = check(cid, did, p)
    titles = [b["title_en"] for b in r["blockers"]]
    assert not r["ready"]
    assert "Missing: District" in titles
    assert "Document missing: Previous mark sheet" in titles and "Document missing: Institution / bonafide certificate" in titles


def test_failed_eligibility_blocks_with_reason():
    cid = "sub-test-0002"
    p = dict(FULL, annual_income=300000)
    did = draft(cid, p, ALL_DOCS).get_json()["draft_id"]
    r = check(cid, did, p)
    elig = [b for b in r["blockers"] if b["type"] == "eligibility"]
    assert elig and "50,000 above" in elig[0]["detail_en"]
    assert c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": p}, headers={"X-Client-Id": cid}).status_code == 409


def test_full_flow_preview_change_submit_final():
    cid, h = "sub-test-0003", {"X-Client-Id": "sub-test-0003"}
    did = draft(cid, FULL, ALL_DOCS).get_json()["draft_id"]
    assert check(cid, did, FULL)["ready"]
    # cannot submit before seeing the preview
    r = c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": FULL}, headers=h)
    assert r.status_code == 409 and r.get_json()["needs_preview"]
    pv = c.get(f"/api/application/{did}/pdf?kind=preview", headers=h)
    assert pv.status_code == 200 and pv.data[:4] == b"%PDF"
    # student asks for a change -> new version; the old preview no longer counts
    v2 = draft(cid, FULL, ALL_DOCS, did, note="Write my full course name", edits={"course": "B.Tech Computer Science"}).get_json()
    assert v2["version"] == 2
    assert c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": FULL}, headers=h).status_code == 409
    c.get(f"/api/application/{did}/pdf", headers=h)
    # explicit confirmation required
    assert c.post(f"/api/application/{did}/submit", json={"profile": FULL}, headers=h).status_code == 400
    s = c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": FULL}, headers=h).get_json()
    assert s["status"] == "submitted" and s["application_ref"].startswith("JSA-APP-") and s["application_ref"].endswith(f"{did:05d}")
    assert "DEMO" in s["message_en"]
    d = c.get(f"/api/application/{did}", headers=h).get_json()
    assert d["status"] == "demo_confirmed" and d["form_data"]["course"] == "B.Tech Computer Science"
    assert d["change_notes"][0]["note"] == "Write my full course name"
    final = c.get(f"/api/application/{did}/pdf?kind=final&download=1", headers=h)
    assert final.status_code == 200 and final.data[:4] == b"%PDF" and "attachment" in final.headers["Content-Disposition"]


def test_after_submit_locked_and_private():
    cid, h = "sub-test-0004", {"X-Client-Id": "sub-test-0004"}
    did = draft(cid, FULL, ALL_DOCS).get_json()["draft_id"]
    assert c.get(f"/api/application/{did}/pdf?kind=final", headers=h).status_code == 409      # no final before submit
    c.get(f"/api/application/{did}/pdf", headers=h)
    c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": FULL}, headers=h)
    assert draft(cid, FULL, ALL_DOCS, did).status_code == 409                                     # cannot edit after submit
    assert c.post(f"/api/application/{did}/submit", json={"confirmed": True, "profile": FULL}, headers=h).status_code == 409
    other = {"X-Client-Id": "someone-else-0001"}
    assert c.get(f"/api/application/{did}/pdf", headers=other).status_code == 404
    assert c.post(f"/api/application/{did}/check", json={}, headers=other).status_code == 404


def test_scheme_without_verified_rules_cannot_be_submitted():
    cid = "sub-test-0005"
    r = c.post("/api/application/draft", json={"scheme_id": "kerala_egrantz", "profile": FULL, "provided_items": []},
               headers={"X-Client-Id": cid}).get_json()
    chk = c.post(f"/api/application/{r['draft_id']}/check", json={"profile": FULL}, headers={"X-Client-Id": cid}).get_json()
    assert not chk["ready"] and chk["blockers"][0]["field"] == "scheme_rules"


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
