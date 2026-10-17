"""Application draft builder (owned by P1).

Fills the draft ONLY with what the citizen gave. Empty fields stay empty and are
listed as missing - nothing is guessed. Sensitive optional fields (community,
disability) are included only if the scheme's rules need them AND the citizen gave them.
"""
from services import readiness_engine, retrieval

BASE_FIELDS = ["name", "state", "district", "education_level", "course", "institution_name", "annual_income"]


def draft_fields(scheme):
    rule_fields = [r["field"] for r in scheme.get("eligibility_rules", [])]
    return list(dict.fromkeys(BASE_FIELDS + rule_fields))


def build(scheme_id, profile, provided_items, edits=None):
    scheme = retrieval.get_scheme(scheme_id)
    if not scheme:
        return None
    profile, edits = profile or {}, edits or {}
    form = {}
    for f in draft_fields(scheme):
        value = edits.get(f, profile.get(f))      # citizen's own edit wins over profile
        form[f] = value if value not in ("",) else None
    readiness = readiness_engine.compute(scheme_id, provided_items, {**profile, **{k: v for k, v in form.items() if v is not None}})
    missing = [f for f, v in form.items() if v is None] + [m["id"] for m in readiness["missing"] if m["kind"] == "document"]
    return {
        "scheme_id": scheme_id, "scheme_name_en": scheme["name_en"], "scheme_name_ml": scheme["name_ml"],
        "form_data": form, "missing_items": list(dict.fromkeys(missing)),
        "readiness_score": readiness["score"], "readiness": readiness,
        "application_method": scheme.get("application_method"), "official_url": scheme.get("official_url"),
        "is_test": scheme.get("is_test", False),
        "message_en": "Your application draft is ready. Review the information and follow the official department's submission process.",
        "message_ml": "നിങ്ങളുടെ അപേക്ഷാ ഡ്രാഫ്റ്റ് തയ്യാറാണ്. വിവരങ്ങൾ പരിശോധിച്ച് ഔദ്യോഗിക വകുപ്പിന്റെ രീതി പിന്തുടരുക.",
    }
