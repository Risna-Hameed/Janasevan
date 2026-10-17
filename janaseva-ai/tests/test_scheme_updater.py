"""Daily scheme updater: discovery, notice reading, honesty limits, change watch, hiding, API.
No internet needed - the fictional demo feed and a fake fetcher are used.  Run: python tests/test_scheme_updater.py"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="janaseva_upd_")
os.environ["SCHEME_AUTO_UPDATE"] = "0"
os.environ["SCHEME_UPDATE_DIR"] = os.path.join(TMP, "updates")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from services import retrieval, scheme_updater as su  # noqa: E402
from services.application_submission import check as submit_check  # noqa: E402
from services.eligibility_engine import check_eligibility  # noqa: E402


class FakeFetcher(su.Fetcher):
    """Serves http(s) pages from a dict; local demo files are read for real."""

    def __init__(self, pages=None):
        super().__init__(su.load_sources())
        self.pages, self.calls = pages or {}, []

    def get(self, url):
        self.calls.append(url)
        if url.startswith("file:"):
            return super().get(url)
        if not self.allowed_url(url):
            raise ValueError("domain not on the allow-list")
        if url not in self.pages:
            raise ConnectionError("offline in tests")
        return "text/html", self.pages[url].encode()


def fresh(name):
    d = os.path.join(TMP, name)
    su.configure(d)
    return d


def auto_records():
    return [s for s in retrieval.load_schemes() if s.get("source_status") == "auto_added"]


def test_demo_feed_adds_new_schemes_and_skips_known():
    fresh("a")
    r = su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today="2026-10-17")
    names = [a["name"] for a in r["added"]]
    assert len(names) == 2 and any("Coastal" in n for n in names) and any("STEM" in n for n in names)
    assert r["skipped_known"] == 1                       # the Central Sector link matched the existing record
    assert not any("Login" in n or "Contact" in n for n in names)   # navigation links ignored
    again = su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today="2026-10-18")
    assert again["added"] == [] and again["seen_again"] == 2         # no duplicates on the next day


def test_notice_facts_are_read_with_evidence_and_never_verified():
    fresh("b")
    su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today="2026-10-17")
    s = next(x for x in auto_records() if "coastal" in x["id"])
    rules = {r["field"]: r for r in s["eligibility_rules"]}
    assert rules["annual_income"]["value"] == 200000 and rules["previous_exam_percentage"]["value"] == 60
    assert rules["education_level"]["value"] == ["UG"] and rules["state"]["value"] == "Kerala"
    assert all(r["verified"] is False and r["auto_read"] for r in s["eligibility_rules"])
    assert "2,00,000" in rules["annual_income"]["evidence"]
    assert s["deadline"] == "30-11-2026" and "10,000" in s["benefits"] and s["benefit_value_inr"] is None
    g = next(x for x in auto_records() if "stem" in x["id"])
    assert {r["field"]: r["value"] for r in g["eligibility_rules"]}["annual_income"] == 450000       # "4.5 lakh"
    assert {r["field"]: r["value"] for r in g["eligibility_rules"]}["education_level"] == ["PG"]
    assert retrieval.freshness(s)["level"] == "new"


def test_auto_added_can_fail_but_never_be_potentially_eligible_or_submitted():
    fresh("c")
    su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today="2026-10-17")
    s = next(x for x in auto_records() if "coastal" in x["id"])
    good = {"state": "Kerala", "education_level": "UG", "annual_income": 120000, "previous_exam_percentage": 75}
    assert check_eligibility(good, s)["status"] == "verification_required"
    assert check_eligibility(dict(good, annual_income=300000), s)["status"] == "condition_not_met"
    res = submit_check(s, good, ["income_cert"], good)
    assert not res["ready"] and res["blockers"][0]["action"] == "official"


def test_nothing_invented_when_notice_says_nothing():
    out = su.read_notice("Applications are invited. See the website for details.", "All India")
    assert out["rules"] == [] and out["deadline"] is None and out["benefit_sentence"] is None


def test_changed_official_document_is_flagged():
    fresh("d")
    url = "https://nitkkr.ac.in/wp-content/uploads/2025/04/Central-Sector-Scheme-Of-Scholarships-For-College-And-University-Students.pdf"
    f = FakeFetcher({url: "<p>Income limit Rs. 4,50,000 per annum.</p>"})
    su.run_update(fetcher=f, today="2026-10-17")
    assert not any(s.get("source_update") for s in retrieval.load_schemes())        # first look = baseline only
    f.pages[url] = "<p>Income limit Rs. 8,00,000 per annum.</p>"
    r = su.run_update(fetcher=f, today="2026-10-18")
    assert "nsp_central_sector" in r["changed"]
    s = retrieval.get_scheme("nsp_central_sector")
    assert s["source_update"]["changed_on"] == "2026-10-18" and retrieval.freshness(s)["level"] == "amber"
    assert su.clear_doc_change("nsp_central_sector") and not retrieval.get_scheme("nsp_central_sector").get("source_update")
    assert any("offline in tests" in e for e in r["errors"])                       # other sites failed, run still finished


def test_only_allow_listed_sites_and_sample_files():
    f = su.Fetcher(su.load_sources())
    assert f.allowed_url("https://www.dcescholarship.kerala.gov.in/x")
    assert not f.allowed_url("https://evil.example.com/scholarship")
    assert not f.allowed_url("https://kerala.gov.in.evil.com/")
    assert not f.allowed_url("file:../config.py") and not f.allowed_url("file:data/schemes.json")
    assert f.allowed_url("file:data/samples/demo_feed/index.html")


def test_hidden_and_unlisted_schemes_leave_the_site():
    d = fresh("e")
    su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today="2026-10-17")
    sid = next(x["id"] for x in auto_records() if "stem" in x["id"])
    assert su.set_hidden(sid, True) and sid not in [s["id"] for s in retrieval.load_schemes()]
    assert su.set_hidden(sid, False) and sid in [s["id"] for s in retrieval.load_schemes()]
    assert not su.set_hidden("nsp_central_sector", True)                  # curated records cannot be hidden here
    # the source page stops listing the scheme -> gone after 3 daily runs
    page = os.path.join(su.SAMPLES_DIR, "demo_feed", "index.html")
    original = open(page, encoding="utf-8").read()
    try:
        open(page, "w", encoding="utf-8").write(original.replace("Demo Girls in STEM Fellowship (fictional)", ""))
        for day in ("2026-10-18", "2026-10-19", "2026-10-20"):
            r = su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"], today=day)
        assert sid in r["unlisted"] and sid not in [s["id"] for s in retrieval.load_schemes()]
    finally:
        open(page, "w", encoding="utf-8").write(original)
    assert os.path.isdir(d)


def test_schedule_is_daily_at_the_set_hour():
    from datetime import datetime
    assert su.next_run_time(None) <= su._now()
    nxt = su.next_run_time("2026-10-17T01:00:00+00:00", 6)          # 6:30 am IST, after today's 6 am slot
    assert nxt.astimezone(su.IST).isoformat().startswith("2026-10-18T06:00")
    nxt = su.next_run_time("2026-10-16T20:00:00+00:00", 6)          # 1:30 am IST -> same morning
    assert nxt.astimezone(su.IST).isoformat().startswith("2026-10-17T06:00")
    assert isinstance(nxt, datetime)


def test_api_public_and_admin_permissions():
    class T(Config):
        DATABASE = os.path.join(TMP, "api.db")
        PRIVATE_UPLOAD_DIR = os.path.join(TMP, "private")
        SCHEME_UPDATE_DIR = os.path.join(TMP, "api_updates")
        SEED_DEMO = True
        TESTING = True
    from app import create_app
    c = create_app(T).test_client()
    su.run_update(fetcher=FakeFetcher(), only_sources=["demo_feed"])
    pub = c.get("/api/schemes/updates").get_json()
    assert len(pub["new_schemes"]) == 2 and pub["last_run"]
    listed = [s["id"] for s in c.get("/api/schemes").get_json()["schemes"]]
    assert any(i.startswith("auto_demo_coastal") for i in listed)

    def login(user):
        cl = create_app(T).test_client()
        me = cl.post("/api/panchayat/login", json={"username": user, "password": "Demo@2026pass"}).get_json()
        return cl, {"X-CSRF-Token": me.get("csrf_token", "")}
    officer, h = login("thanal.officer")
    assert officer.get("/api/panchayat/scheme-updates").status_code == 403
    admin, h = login("thanal.admin")
    st = admin.get("/api/panchayat/scheme-updates").get_json()
    sid = st["auto_added"][0]["id"]
    assert admin.post(f"/api/panchayat/scheme-updates/{sid}/hide", json={"hidden": True}).status_code == 403   # no CSRF
    assert admin.post(f"/api/panchayat/scheme-updates/{sid}/hide", json={"hidden": True}, headers=h).status_code == 200
    assert len(c.get("/api/schemes/updates").get_json()["new_schemes"]) == 1


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
