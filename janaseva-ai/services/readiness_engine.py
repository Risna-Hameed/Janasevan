"""Application readiness score (owned by P1).

Readiness = completed required items / total required items x 100.
It measures how COMPLETE the application is - never the chance of approval.
Required items = the scheme's mandatory documents + the profile fields its rules need.
"""
from services import retrieval

NOTE_EN = "This score measures application completeness, not your probability of approval."
NOTE_ML = "ഈ സ്കോർ അപേക്ഷ എത്രത്തോളം പൂർണ്ണമാണ് എന്ന് മാത്രം കാണിക്കുന്നു; അംഗീകാര സാധ്യതയല്ല."


def compute(scheme_id, provided_items=None, profile=None):
    scheme = retrieval.get_scheme(scheme_id)
    if not scheme:
        return None
    provided = set(provided_items or [])
    profile = profile or {}

    items = []
    for d in scheme.get("required_documents", []):
        if d.get("mandatory"):
            items.append({"id": d["id"], "kind": "document", "name_en": d["name_en"], "name_ml": d["name_ml"],
                          "done": d["id"] in provided})
    for field in sorted({r["field"] for r in scheme.get("eligibility_rules", []) if r.get("mandatory", True)}):
        value = profile.get(field)
        items.append({"id": field, "kind": "field", "name_en": field.replace("_", " "), "name_ml": field.replace("_", " "),
                      "done": value not in (None, "")})

    total = len(items)
    done = [i for i in items if i["done"]]
    missing = [i for i in items if not i["done"]]
    needs_verification = []
    if scheme.get("source_status") != "verified" and not scheme.get("is_test"):
        needs_verification.append({"id": "scheme_rules", "name_en": "Scheme rules and document list",
                                   "name_ml": "പദ്ധതി നിബന്ധനകളും രേഖകളുടെ പട്ടികയും"})
    if not scheme.get("required_documents"):
        needs_verification.append({"id": "document_list", "name_en": "Official document list",
                                   "name_ml": "ഔദ്യോഗിക രേഖകളുടെ പട്ടിക"})

    next_en = [f"Get your {m['name_en'].lower()}." if m["kind"] == "document" else f"Add your {m['name_en']} to your profile." for m in missing]
    next_ml = [f"{m['name_ml']} തയ്യാറാക്കുക." if m["kind"] == "document" else f"പ്രൊഫൈലിൽ {m['name_ml']} ചേർക്കുക." for m in missing]
    if not missing:
        next_en, next_ml = ["Review your draft and follow the official application process."], ["ഡ്രാഫ്റ്റ് പരിശോധിച്ച് ഔദ്യോഗിക രീതിയിൽ അപേക്ഷിക്കുക."]

    return {
        "scheme_id": scheme_id,
        "score": round(len(done) / total * 100) if total else 0,
        "total": total, "completed": done, "missing": missing,
        "mandatory_missing": [m["id"] for m in missing],   # every counted item is mandatory
        "needs_verification": needs_verification,
        "next_steps_en": next_en, "next_steps_ml": next_ml,
        "note_en": NOTE_EN, "note_ml": NOTE_ML,
        "approval_probability": None,
        "approval_note_en": "Approval probability unavailable: validated historical data is not available.",
        "approval_note_ml": "അംഗീകാര സാധ്യത ലഭ്യമല്ല: സാധുതയുള്ള മുൻകാല ഡാറ്റ ലഭ്യമല്ല.",
    }
