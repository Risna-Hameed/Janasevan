"""Tests for the eligibility engine and BestFit ranker.  Run: python tests/test_engine_ranker.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.eligibility_engine import check_eligibility, evaluate_rule
from services.ranker import rank, topsis

STUDENT = {"state": "Kerala", "education_level": "UG", "annual_income": 120000, "course_mode": "regular",
           "class12_percentile": 92, "receives_other_scholarship": False, "gender": "female",
           "institution_aicte_approved": True}


def test_all_conditions_met():                       # test case 1
    r = check_eligibility(STUDENT, "test_ug_merit")
    assert r["status"] == "potentially_eligible" and len(r["met"]) == 3


def test_mandatory_condition_fails_with_counterfactual():   # test case 2
    r = check_eligibility({**STUDENT, "annual_income": 300000}, "test_ug_merit")
    assert r["status"] == "condition_not_met"
    assert "50,000 above" in r["counterfactuals"][0]["text_en"]


def test_incomplete_profile_is_unknown_not_met():    # test case 3
    r = check_eligibility({"state": "Kerala"}, "test_ug_merit")
    assert r["status"] == "more_info_required"
    assert set(r["fields_needed"]) == {"education_level", "annual_income"}


def test_verified_real_scheme():
    assert check_eligibility(STUDENT, "nsp_central_sector")["status"] == "potentially_eligible"


def test_unverified_scheme_never_says_eligible():
    r = check_eligibility(STUDENT, "aicte_pragati")
    assert r["status"] == "verification_required" and len(r["met"]) == 4


def test_scheme_without_rules():                     # test case 4
    assert check_eligibility(STUDENT, "kerala_egrantz")["status"] == "verification_required"


def test_type_handling():
    rule = {"id": "x", "field": "annual_income", "operator": "<=", "value": 200000}
    assert evaluate_rule(rule, {"annual_income": "1,50,000"})[0] == "met"
    assert evaluate_rule(rule, {"annual_income": "about two lakh"})[0] == "unknown"
    assert evaluate_rule({"id": "y", "field": "bpl", "operator": "==", "value": True}, {"bpl": "yes"})[0] == "met"


def test_profile_change_changes_result():            # test case 10
    assert check_eligibility({**STUDENT, "education_level": "PG"}, "test_ug_merit")["status"] == "condition_not_met"


def test_topsis_worked_example():
    rows = [dict(zip("abcde", r)) for r in ([20000, 100, 100, 0, 1], [50000, 60, 100, 2, 1], [30000, 80, 67, 1, 0])]
    crit = [("a", True), ("b", True), ("c", True), ("d", False), ("e", True)]
    s = topsis(rows, dict(zip("abcde", [.30, .25, .20, .15, .10])), crit)
    assert [round(x, 3) for x in s] == [0.542, 0.525, 0.378]


def test_rank_filters_and_explains():
    out = rank(STUDENT, provided_docs=["income_cert", "marksheet", "institution_cert"])
    ids = [r["scheme_id"] for r in out["results"]]
    assert "test_girls_pg" in [e["scheme_id"] for e in out["excluded"]]   # PG-only scheme filtered out
    assert ids[0] in ("test_ug_merit", "nsp_central_sector")
    assert "benefit" in out["criteria_dropped"]                          # unverified amounts never guessed
    assert all(r["explanation_en"].startswith("#") for r in out["results"])


def test_more_documents_improves_rank_score():      # test case 11 (readiness recalculated)
    before = {r["scheme_id"]: r["score"] for r in rank(STUDENT, [])["results"]}
    after = {r["scheme_id"]: r["score"] for r in rank(STUDENT, ["income_cert", "marksheet", "institution_cert"])["results"]}
    assert after["test_ug_merit"] > before["test_ug_merit"]


if __name__ == "__main__":
    bad = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try:
                f(); print("PASS", n)
            except AssertionError as e:
                bad += 1; print("FAIL", n, e)
    sys.exit(1 if bad else 0)
