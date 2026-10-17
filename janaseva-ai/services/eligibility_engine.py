"""Rule-based eligibility engine.

The LLM never decides eligibility. This module compares the student's
profile with the rules stored in data/schemes.json and explains every result.

Each rule gives one of three answers:
    met      - the profile satisfies the rule
    not_met  - the profile breaks the rule
    unknown  - the profile does not have this field yet (never assumed met)

Overall status (checked in this order):
    condition_not_met      - any mandatory rule is not_met
    more_info_required     - any mandatory rule is unknown
    verification_required  - rules pass, but the scheme or a rule is not verified
    potentially_eligible   - every mandatory rule is met and the scheme is verified
"""
from services import retrieval

MET, NOT_MET, UNKNOWN = "met", "not_met", "unknown"

STATUS_TEXT = {
    "potentially_eligible": ("Potentially Eligible", "അർഹതയുണ്ടാകാൻ സാധ്യതയുണ്ട്"),
    "condition_not_met": ("A Mandatory Condition Is Not Met", "ഒരു നിർബന്ധിത നിബന്ധന പാലിക്കുന്നില്ല"),
    "more_info_required": ("More Information Required", "കൂടുതൽ വിവരങ്ങൾ ആവശ്യമാണ്"),
    "verification_required": ("Official Verification Required", "ഔദ്യോഗിക പരിശോധന ആവശ്യമാണ്"),
}

FIELD_LABELS = {
    "annual_income": ("annual family income", "വാർഷിക കുടുംബ വരുമാനം"),
    "education_level": ("education level", "വിദ്യാഭ്യാസ നില"),
    "previous_exam_percentage": ("marks in your previous exam", "മുൻ പരീക്ഷയിലെ മാർക്ക്"),
    "class12_percentile": ("Class 12 percentile", "പ്ലസ് ടു പെർസന്റൈൽ"),
    "state": ("state", "സംസ്ഥാനം"),
    "gender": ("gender", "ലിംഗം"),
    "community": ("community (optional)", "സമുദായം (ഐച്ഛികം)"),
    "disability_percent": ("disability percentage", "ഭിന്നശേഷി ശതമാനം"),
    "institution_type": ("institution type", "സ്ഥാപന തരം"),
    "institution_aicte_approved": ("whether your institution is AICTE-approved", "സ്ഥാപനം AICTE അംഗീകൃതമാണോ"),
    "course_mode": ("regular or distance course", "റെഗുലർ / വിദൂര കോഴ്സ്"),
    "year_of_study": ("year of study", "പഠന വർഷം"),
    "bpl": ("whether your family is BPL", "ബിപിഎൽ കുടുംബമാണോ"),
    "receives_other_scholarship": ("whether you already get another scholarship", "മറ്റൊരു സ്കോളർഷിപ്പ് ലഭിക്കുന്നുണ്ടോ"),
}

NUMERIC_OPS = {"<", "<=", ">", ">="}


def _missing(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_number(v):
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _to_bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


def _norm(v):
    return str(v).strip().lower()


def evaluate_rule(rule, profile):
    """Step 1 of the algorithm: compare ONE rule with the profile -> (answer, profile value)."""
    field, op, target = rule.get("field"), rule.get("operator"), rule.get("value")
    actual = (profile or {}).get(field)
    if _missing(actual):
        return UNKNOWN, None                       # missing data is never treated as met

    if op in NUMERIC_OPS:
        a, t = _to_number(actual), _to_number(target)
        if a is None or t is None:
            return UNKNOWN, actual                 # e.g. "about two lakh" -> ask again
        ok = {"<": a < t, "<=": a <= t, ">": a > t, ">=": a >= t}[op]
        return (MET if ok else NOT_MET), a

    if isinstance(target, bool):                   # yes/no rules
        a = _to_bool(actual)
        if a is None:
            return UNKNOWN, actual
        ok = (a == target) if op == "==" else (a != target)
        return (MET if ok else NOT_MET), a

    if op == "in":
        ok = _norm(actual) in {_norm(x) for x in target}
        return (MET if ok else NOT_MET), actual
    if op in ("==", "!="):
        if _to_number(target) is not None and _to_number(actual) is not None:
            same = _to_number(actual) == _to_number(target)
        else:
            same = _norm(actual) == _norm(target)
        return (MET if same == (op == "==") else NOT_MET), actual
    return UNKNOWN, actual                         # unsupported operator: never guess


def _fmt(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{int(v):,}" if float(v).is_integer() else str(v)
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


def _counterfactual(rule, actual):
    """'What would change my result?' - explain the gap for a failed rule."""
    field, op, target = rule["field"], rule["operator"], rule["value"]
    label_en, label_ml = FIELD_LABELS.get(field, (field.replace("_", " "), field))
    if op in NUMERIC_OPS and isinstance(actual, (int, float)):
        gap = abs(_to_number(target) - actual)
        word = "above" if op in ("<", "<=") else "below"
        word_ml = "കൂടുതലാണ്" if op in ("<", "<=") else "കുറവാണ്"
        return {"rule_id": rule["id"], "field": field,
                "text_en": f"Your {label_en} ({_fmt(actual)}) is {_fmt(gap)} {word} the required limit ({op} {_fmt(target)}).",
                "text_ml": f"നിങ്ങളുടെ {label_ml} ({_fmt(actual)}) ആവശ്യമായ പരിധിയേക്കാൾ ({op} {_fmt(target)}) {_fmt(gap)} {word_ml}."}
    return {"rule_id": rule["id"], "field": field,
            "text_en": f"This scheme needs {label_en}: {_fmt(target)}. Yours is {_fmt(actual)}.",
            "text_ml": f"ഈ പദ്ധതിക്ക് {label_ml}: {_fmt(target)} ആവശ്യമാണ്. നിങ്ങളുടേത് {_fmt(actual)}."}


def check_eligibility(profile, scheme):
    """Evaluate every rule of one scheme and return an explained result."""
    if isinstance(scheme, str):
        scheme = retrieval.get_scheme(scheme)
    if not scheme:
        return {"status": "verification_required", "error": "Scheme not found",
                "met": [], "not_met": [], "unknown": [], "counterfactuals": []}

    met, not_met, unknown, counterfactuals = [], [], [], []
    for rule in scheme.get("eligibility_rules", []):
        answer, actual = evaluate_rule(rule, profile)
        item = {"rule_id": rule["id"], "field": rule["field"], "mandatory": rule.get("mandatory", True),
                "description_en": rule.get("description_en"), "description_ml": rule.get("description_ml"),
                "your_value": actual, "rule_verified": rule.get("verified", False)}
        if answer == MET:
            met.append(item)
        elif answer == NOT_MET:
            not_met.append(item)
            counterfactuals.append(_counterfactual(rule, actual))
        else:
            label_en, label_ml = FIELD_LABELS.get(rule["field"], (rule["field"], rule["field"]))
            item["ask_en"], item["ask_ml"] = f"Please tell us your {label_en}.", f"നിങ്ങളുടെ {label_ml} അറിയിക്കുക."
            unknown.append(item)

    rules = scheme.get("eligibility_rules", [])
    trusted = scheme.get("source_status") in ("verified", "test") and all(r.get("verified") for r in rules)

    # Step 3: decide the overall status (order matters: a definite failure beats missing data)
    if not rules:
        status = "verification_required"
    elif any(i["mandatory"] for i in not_met):
        status = "condition_not_met"
    elif any(i["mandatory"] for i in unknown):
        status = "more_info_required"
    elif not trusted:
        status = "verification_required"
    else:
        status = "potentially_eligible"

    en, ml = STATUS_TEXT[status]
    return {
        "scheme_id": scheme["id"], "scheme_name_en": scheme.get("name_en"), "scheme_name_ml": scheme.get("name_ml"),
        "status": status, "status_en": en, "status_ml": ml,
        "met": met, "not_met": not_met, "unknown": unknown,
        "counterfactuals": counterfactuals,
        "fields_needed": sorted({i["field"] for i in unknown}),
        "source_status": scheme.get("source_status"), "is_test": scheme.get("is_test", False),
        "official_url": scheme.get("official_url"),
        "note_en": "This is not a final decision. Eligibility is confirmed only by the official department.",
        "note_ml": "ഇത് അന്തിമ തീരുമാനമല്ല. ഔദ്യോഗിക വകുപ്പ് മാത്രമേ അർഹത സ്ഥിരീകരിക്കൂ.",
    }


def match_all(profile, include_tests=True):
    """Check every scheme. Sorted: potentially eligible first, then verify, more info, not met."""
    order = {"potentially_eligible": 0, "verification_required": 1, "more_info_required": 2, "condition_not_met": 3}
    results = [check_eligibility(profile, s) for s in retrieval.load_schemes(include_tests)]
    return sorted(results, key=lambda r: (order[r["status"]], -len(r["met"])))
