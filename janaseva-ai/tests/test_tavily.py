"""Tavily scheme finder: filtering, reading notices, honesty limits, API.
No internet or credits needed - Tavily's answer is faked with the kinds of pages it really returns.
Run: python tests/test_tavily.py"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="janaseva_tavily_")
os.environ["TAVILY_STORE_PATH"] = os.path.join(TMP, "store.json")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from services import retrieval, tavily_scraper as tv  # noqa: E402
from services.application_submission import check as submit_check  # noqa: E402
from services.eligibility_engine import check_eligibility  # noqa: E402

NOTICE = ("Post Matric Scholarship for Scheduled Tribe Students\n"
          "Applications are invited from ST students studying in undergraduate degree courses (B.Sc., B.Com., B.Tech). "
          "Eligibility: The annual family income should not exceed Rs. 2,50,000. "
          "Students should have secured 50% marks or above in the qualifying examination. "
          "The scholarship amount is Rs. 12,000 per annum. Last date for online applications: 31-12-2026.")
PAGES = [  # what Tavily returned for "Kerala scholarship 2026 notification"
    {"title": "NSP - National Scholarship Portal", "url": "https://scholarships.gov.in/Students",
     "content": "National Scholarship Portal students login and registration", "raw_content": "Student login ..."},
    {"title": "NSP - National Scholarship Portal", "url": "https://scholarships.gov.in/Institute",
     "content": "Institute login", "raw_content": "Institute login ..."},
    {"title": "EDUPostMatricST.pdf", "url": "https://scholarships.gov.in/public/schemeGuidelines/EDUPostMatricST.pdf",
     "content": NOTICE[:200], "raw_content": NOTICE},
    {"title": "Begum Hazrat Mahal National Scholarship", "url": "https://scholarships.gov.in/public/1053_G.pdf",
     "content": "Scholarship for girl students", "raw_content": "Begum Hazrat Mahal National Scholarship\n"
     "Only for girl students of classes 9 to 12. Annual income of parents from all sources should not exceed Rs. 2.00 lakh."},
    {"title": "Central Sector Scheme of Scholarships for College and University Students",   # already in the catalog
     "url": "https://scholarships.gov.in/public/csss.pdf", "content": "merit scholarship", "raw_content": "eligibility ..."},
    {"title": "Scholarship news", "url": "https://random-blog.example.com/scholarship-2026",   # not an official domain
     "content": "scholarship eligibility", "raw_content": "eligibility income Rs. 1,00,000"},
]


def fake(pages):
    return lambda q: {"results": pages}


def fresh(name):
    tv.configure(os.path.join(TMP, name + ".json"))


def found():
    return [s for s in retrieval.load_schemes() if s.get("source_status") == "found_online"]


def test_keeps_only_new_official_scheme_pages():
    fresh("a")
    r = tv.scrape(search=fake(PAGES), key="x", today="2026-10-17")
    names = sorted(a["name"] for a in r["added"])
    assert names == ["Begum Hazrat Mahal National Scholarship", "Post Matric Scholarship for Scheduled Tribe Students"], names
    assert r["already_known"] == 1          # Central Sector matched the curated record
    assert r["skipped"] == 3                # two portal pages + the non-government blog
    again = tv.scrape(search=fake(PAGES), key="x", today="2026-10-18", force=True)
    assert again["added"] == [] and again["already_known"] == 3          # no duplicates on the next search


def test_name_comes_from_text_when_title_is_a_file_name():
    assert tv.scheme_name("EDUPostMatricST.pdf", NOTICE) == "Post Matric Scholarship for Scheduled Tribe Students"
    assert tv.scheme_name("1053_G.pdf", "nothing useful here") is None


def test_facts_copied_with_evidence_and_never_verified():
    fresh("b")
    tv.scrape(search=fake(PAGES), key="x", today="2026-10-17")
    s = next(x for x in found() if "post_matric" in x["id"])
    rules = {r["field"]: r for r in s["eligibility_rules"]}
    assert rules["annual_income"]["value"] == 250000 and rules["previous_exam_percentage"]["value"] == 50
    assert rules["education_level"]["value"] == ["UG"]
    assert "2,50,000" in rules["annual_income"]["evidence"]
    assert all(r["verified"] is False for r in s["eligibility_rules"])
    assert s["deadline"] == "31-12-2026" and "12,000" in s["benefits"] and s["benefit_value_inr"] is None
    g = next(x for x in found() if "begum" in x["id"])
    assert {r["field"]: r["value"] for r in g["eligibility_rules"]} == {"annual_income": 200000, "gender": "female"}
    assert retrieval.freshness(s)["level"] == "new"


def test_never_potentially_eligible_and_not_submittable():
    fresh("c")
    tv.scrape(search=fake(PAGES), key="x", today="2026-10-17")
    s = next(x for x in found() if "post_matric" in x["id"])
    good = {"state": "Kerala", "education_level": "UG", "annual_income": 120000, "previous_exam_percentage": 75}
    assert check_eligibility(good, s)["status"] == "verification_required"
    assert check_eligibility(dict(good, annual_income=400000), s)["status"] == "condition_not_met"
    res = submit_check(s, good, [], good)
    assert not res["ready"] and res["blockers"][0]["action"] == "official"
    assert res["blockers"][0]["url"].startswith("https://scholarships.gov.in")


def test_nothing_invented():
    out = tv.read_notice("Applications are invited. See the website for details.", "All India")
    assert out["rules"] == [] and out["deadline"] is None and out["benefit_sentence"] is None


def test_errors_and_limits():
    fresh("d")
    assert not tv.scrape(key="")["ok"]                                         # no key: clear message, no crash

    def broken(q):
        raise ConnectionError("no internet")
    r = tv.scrape(search=broken, key="x")
    assert not r["ok"] and "internet connection" in r["error"]
    assert tv.scrape(search=fake(PAGES), key="x")["ok"]                        # a failed search does not block a retry
    r = tv.scrape(search=fake(PAGES), key="x")                                  # a search just succeeded
    assert not r["ok"] and r.get("wait")


def test_hide_and_api():
    class T(Config):
        DATABASE = os.path.join(TMP, "api.db")
        PRIVATE_UPLOAD_DIR = os.path.join(TMP, "private")
        TAVILY_STORE_PATH = os.path.join(TMP, "api_store.json")
        SEED_DEMO = False
        TESTING = True
    from app import create_app
    c = create_app(T).test_client()
    tv.scrape(search=fake(PAGES), key="x", today="2026-10-17")
    st = c.get("/api/schemes/found").get_json()
    assert len(st["found"]) == 2
    ids = [s["id"] for s in c.get("/api/schemes").get_json()["schemes"]]
    assert st["found"][0]["id"] in ids
    sid = st["found"][0]["id"]
    assert c.post(f"/api/schemes/found/{sid}/hide", json={"hidden": True}).status_code == 200
    assert sid not in [s["id"] for s in c.get("/api/schemes").get_json()["schemes"]]
    assert c.post("/api/schemes/found/nsp_central_sector/hide").status_code == 404
    old = os.environ.pop("TAVILY_API_KEY", None)
    try:
        assert c.post("/api/schemes/find-new").status_code == 503                 # no key -> friendly error
    finally:
        if old:
            os.environ["TAVILY_API_KEY"] = old


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        try:
            fn(); print("PASS", name)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            failed += 1; print("FAIL", name, type(e).__name__, e)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
