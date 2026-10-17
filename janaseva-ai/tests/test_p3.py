"""P3 tests. Run from the project root:  python -m pytest tests/test_p3.py  (or: python tests/test_p3.py)"""
import json
import os
import tempfile as _tf
os.environ.setdefault("TAVILY_STORE_PATH", os.path.join(_tf.mkdtemp(prefix="janaseva_tv_"), "t.json"))  # never the real store
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_API_KEY"] = ""  # test case 8: app must work with no API key
import tempfile  # noqa: E402
_TMP = tempfile.mkdtemp(prefix="janaseva_p3_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")          # never the real database
os.environ["PRIVATE_UPLOAD_DIR"] = os.path.join(_TMP, "private")

from app import app
from services import chatbot, document_service, retrieval

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "samples")
client = app.test_client()


def test_schemes_load_and_real_records_not_invented():
    schemes = retrieval.load_schemes()
    assert len(schemes) >= 5
    for s in schemes:
        refs = {src["ref"] for src in s.get("sources", [])}
        for r in s["eligibility_rules"]:
            assert "verified" in r and "source_ref" in r          # every rule says where it came from
            if not s["is_test"]:
                assert r["source_ref"] in refs                    # and that source is listed
        if s["source_status"] == "verified":
            assert s["last_verified"] and all(r["verified"] for r in s["eligibility_rules"])


def test_malayalam_question_asks_for_profile():  # demo step 2-3
    r = client.post("/api/chat", json={"message": "എനിക്ക് ലഭിക്കാവുന്ന സ്കോളർഷിപ്പുകൾ ഏതൊക്കെയാണ്?"}).get_json()
    assert r["lang"] == "ml" and r["intent"] == "recommend" and r["follow_up"]


def test_recommend_with_profile_returns_sources():
    profile = {"education_level": "UG", "annual_income": 120000, "state": "Kerala"}
    r = client.post("/api/chat", json={"message": "Which scholarships for B.Tech students?", "profile": profile}).get_json()
    assert r["facts"] and r["sources"] and r["mode"] == "fallback"


def test_documents_intent():
    r = client.post("/api/chat", json={"message": "What documents do I need?", "scheme_id": "test_ug_merit"}).get_json()
    assert r["intent"] == "documents" and "Income certificate" in r["facts"][0]["documents"]


def test_unrelated_question():  # test case 9
    r = client.post("/api/chat", json={"message": "who won the cricket match yesterday"}).get_json()
    assert r["intent"] == "unrelated"


def test_missing_url_and_deadline_handled():  # test cases 5 and 13
    f = retrieval.scheme_facts(retrieval.get_scheme("test_ug_merit"))
    assert f["official_url"] is None and "confirm" in f["deadline"].lower()
    assert retrieval.freshness(retrieval.get_scheme("kerala_mundassery"))["level"] == "red"
    assert retrieval.freshness(retrieval.get_scheme("nsp_central_sector"))["level"] == "green"


def test_bad_chat_input():
    assert client.post("/api/chat", json={"message": 5}).status_code == 400


def test_ocr_reads_sample_and_flags_mismatch():
    with open(os.path.join(SAMPLES, "sample_income_certificate.png"), "rb") as fh:
        r = client.post("/api/documents/analyze", data={
            "file": (fh, "cert.png"), "reference": json.dumps({"name": "Anu Demo Kumar", "annual_income": 150000})},
            content_type="multipart/form-data").get_json()
    if not document_service.OCR_AVAILABLE:
        assert r["manual_entry_required"]
        return
    assert r["fields"]["annual_income"] == 120000
    assert [m["field"] for m in r["mismatches"] if m["severity"] == "high"] == ["annual_income"]


def test_unreadable_upload_rejected():  # test case 7
    r = client.post("/api/documents/analyze", data={"file": (__import__("io").BytesIO(b"not an image"), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["manual_entry_required"]


def test_consistency_name_mismatch():
    issues = document_service.consistency_check({"name": "Anju Varghese"}, {"name": "Anu Demo Kumar"})
    assert issues[0]["field"] == "name"


def test_packet_pdf():
    r = client.post("/api/packet", json={"scheme_id": "test_ug_merit", "provided_items": ["income_cert"]})
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    assert client.post("/api/packet", json={"scheme_id": "nope"}).status_code == 404


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except AssertionError as e:
                failed += 1; print("FAIL", name, e)
    sys.exit(1 if failed else 0)
