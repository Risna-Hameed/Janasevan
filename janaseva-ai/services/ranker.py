"""BestFit: ranks the scholarships a student can apply for, using TOPSIS.

This is a ranking of FIT, not a prediction of approval.

Algorithm
  1. Filter    - run the eligibility engine on every scheme; drop any scheme
                 where a mandatory condition is not met.
  2. Features  - build one row per remaining scheme:
                   benefit    verified amount in INR (higher is better)
                   certainty  share of rules proven met (higher is better)
                   readiness  share of mandatory documents already held (higher)
                   missing    number of mandatory documents still missing (lower)
                   renewable  1 yes / 0.5 unknown / 0 no (higher)
                   trust      1 if the scheme is verified, else 0 (higher)
     A criterion with ANY missing value (e.g. an unverified amount) is left out
     for everyone, and the weights are re-balanced. Values are never guessed.
  3. TOPSIS    - normalise each column, apply weights, measure distance to the
                 ideal and to the worst option; score = d_worst / (d_best + d_worst).
  4. Explain   - list where each scheme beats or trails the others, and warn
                 about schemes that cannot be combined with others.
"""
import math

from services import retrieval
from services.eligibility_engine import check_eligibility

# (column, higher_is_better)
CRITERIA = [("benefit", True), ("certainty", True), ("readiness", True),
            ("missing", False), ("renewable", True), ("trust", True)]

WEIGHT_PRESETS = {
    "balanced": {"benefit": 0.25, "certainty": 0.20, "readiness": 0.20, "missing": 0.10, "renewable": 0.10, "trust": 0.15},
    "money":    {"benefit": 0.50, "certainty": 0.15, "readiness": 0.10, "missing": 0.05, "renewable": 0.10, "trust": 0.10},
    "easy":     {"benefit": 0.10, "certainty": 0.20, "readiness": 0.35, "missing": 0.20, "renewable": 0.05, "trust": 0.10},
    "sure":     {"benefit": 0.10, "certainty": 0.40, "readiness": 0.10, "missing": 0.05, "renewable": 0.05, "trust": 0.30},
}

LABELS = {
    "benefit": ("higher amount", "കൂടുതൽ തുക"),
    "certainty": ("more conditions confirmed", "കൂടുതൽ നിബന്ധനകൾ സ്ഥിരീകരിച്ചു"),
    "readiness": ("more of your documents ready", "കൂടുതൽ രേഖകൾ തയ്യാർ"),
    "missing": ("fewer documents missing", "കുറവ് രേഖകൾ ബാക്കി"),
    "renewable": ("renewable every year", "എല്ലാ വർഷവും പുതുക്കാം"),
    "trust": ("officially verified information", "ഔദ്യോഗികമായി പരിശോധിച്ച വിവരം"),
}

WEAK = {
    "benefit": ("lower amount", "കുറഞ്ഞ തുക"),
    "certainty": ("more conditions still unconfirmed", "കൂടുതൽ നിബന്ധനകൾ സ്ഥിരീകരിക്കാനുണ്ട്"),
    "readiness": ("fewer of your documents ready", "കുറവ് രേഖകൾ തയ്യാർ"),
    "missing": ("more documents missing", "കൂടുതൽ രേഖകൾ ബാക്കി"),
    "renewable": ("not renewable or unknown", "പുതുക്കാനാവില്ല / അറിയില്ല"),
    "trust": ("information not yet verified", "വിവരം ഇതുവരെ പരിശോധിച്ചിട്ടില്ല"),
}


def build_features(scheme, elig, provided_docs):
    """Step 2: turn one scheme + eligibility result + documents held into numbers."""
    rules = scheme.get("eligibility_rules", [])
    mandatory_docs = [d["id"] for d in scheme.get("required_documents", []) if d.get("mandatory")]
    held = [d for d in mandatory_docs if d in provided_docs]
    benefit = scheme.get("benefit_value_inr") if scheme.get("source_status") in ("verified", "test") else None
    renewable = scheme.get("renewable")
    return {
        "benefit": float(benefit) if isinstance(benefit, (int, float)) else None,
        "certainty": len(elig["met"]) / len(rules) if rules else 0.0,
        "readiness": len(held) / len(mandatory_docs) if mandatory_docs else 0.0,
        "missing": float(len(mandatory_docs) - len(held)),
        "renewable": 1.0 if renewable is True else 0.0 if renewable is False else 0.5,
        "trust": 1.0 if elig["source_status"] in ("verified", "test") else 0.0,
        "_missing_docs": [d for d in mandatory_docs if d not in provided_docs],
        "_docs_listed": bool(mandatory_docs),
    }


def topsis(rows, weights, criteria):
    """Step 3: classic TOPSIS. rows = list of dicts; returns a score 0..1 per row."""
    if len(rows) == 1:
        return [1.0]
    # 3a. vector-normalise each column (an all-zero column stays zero)
    norms = {c: math.sqrt(sum(r[c] ** 2 for r in rows)) for c, _ in criteria}
    v = [{c: (r[c] / norms[c] if norms[c] else 0.0) * weights[c] for c, _ in criteria} for r in rows]
    # 3b. ideal = best value per column, worst = worst value per column
    ideal = {c: (max if hib else min)(x[c] for x in v) for c, hib in criteria}
    worst = {c: (min if hib else max)(x[c] for x in v) for c, hib in criteria}
    scores = []
    for x in v:
        d_best = math.sqrt(sum((x[c] - ideal[c]) ** 2 for c, _ in criteria))
        d_worst = math.sqrt(sum((x[c] - worst[c]) ** 2 for c, _ in criteria))
        scores.append(d_worst / (d_best + d_worst) if (d_best + d_worst) else 1.0)  # all identical -> tie
    return scores


def _reasons(row, rows, criteria):
    """Step 4: where is this scheme the best / the worst among the candidates?"""
    good, bad = [], []
    for c, hib in criteria:
        values = [r[c] for r in rows]
        best, worst = (max(values), min(values)) if hib else (min(values), max(values))
        if best == worst:
            continue
        if row[c] == best:
            good.append(c)
        elif row[c] == worst:
            bad.append(c)
    return good, bad


def rank(profile, provided_docs=None, preset="balanced", weights=None, include_tests=True):
    """Return the ranked list of schemes the student can consider, with reasons."""
    provided_docs = set(provided_docs or [])
    weights = dict(weights or WEIGHT_PRESETS.get(preset, WEIGHT_PRESETS["balanced"]))

    # Step 1: filter with the eligibility engine
    candidates, excluded = [], []
    for scheme in retrieval.load_schemes(include_tests):
        elig = check_eligibility(profile, scheme)
        if elig["status"] == "condition_not_met":
            excluded.append({"scheme_id": scheme["id"], "name_en": scheme["name_en"],
                             "why_en": [c["text_en"] for c in elig["counterfactuals"]],
                             "why_ml": [c["text_ml"] for c in elig["counterfactuals"]]})
            continue
        if not scheme.get("eligibility_rules"):
            excluded.append({"scheme_id": scheme["id"], "name_en": scheme["name_en"],
                             "why_en": ["Rules not yet verified - cannot be checked."],
                             "why_ml": ["നിബന്ധനകൾ ഇതുവരെ പരിശോധിച്ചിട്ടില്ല."]})
            continue
        candidates.append((scheme, elig, build_features(scheme, elig, provided_docs)))

    if not candidates:
        return {"results": [], "excluded": excluded, "criteria_used": [], "criteria_dropped": [],
                "note_en": "No scheme could be recommended from the information given.",
                "note_ml": "നൽകിയ വിവരങ്ങളിൽ നിന്ന് ഒരു പദ്ധതിയും നിർദ്ദേശിക്കാനായില്ല."}

    # Step 2b: drop criteria with missing values (never guess) and re-balance weights
    rows = [f for _, _, f in candidates]
    used = [(c, hib) for c, hib in CRITERIA if all(r[c] is not None for r in rows)]
    dropped = [c for c, _ in CRITERIA if (c, True) not in used and (c, False) not in used]
    total = sum(weights[c] for c, _ in used) or 1.0
    w = {c: weights[c] / total for c, _ in used}

    scores = topsis(rows, w, used)
    results = []
    for (scheme, elig, f), score in zip(candidates, scores):
        good, bad = _reasons(f, rows, used)
        warnings_en, warnings_ml = [], []
        if "ALL_OTHER_SCHOLARSHIPS" in scheme.get("mutually_exclusive_with", []):
            warnings_en.append("Cannot be combined with any other scholarship - choose one.")
            warnings_ml.append("മറ്റൊരു സ്കോളർഷിപ്പുമായി ഒരുമിച്ച് ലഭിക്കില്ല - ഒന്ന് തിരഞ്ഞെടുക്കുക.")
        if elig["status"] != "potentially_eligible":
            warnings_en.append(elig["status_en"] + ".")
            warnings_ml.append(elig["status_ml"] + ".")
        if not f["_docs_listed"]:
            warnings_en.append("Document list not verified.")
            warnings_ml.append("രേഖകളുടെ പട്ടിക പരിശോധിച്ചിട്ടില്ല.")
        results.append({
            "scheme_id": scheme["id"], "name_en": scheme["name_en"], "name_ml": scheme["name_ml"],
            "score": round(score, 3), "eligibility_status": elig["status"],
            "features": {k: v for k, v in f.items() if not k.startswith("_")},
            "missing_documents": f["_missing_docs"], "fields_needed": elig["fields_needed"],
            "strengths_en": [LABELS[c][0] for c in good], "strengths_ml": [LABELS[c][1] for c in good],
            "weaknesses_en": [WEAK[c][0] for c in bad], "weaknesses_ml": [WEAK[c][1] for c in bad],
            "warnings_en": warnings_en, "warnings_ml": warnings_ml,
            "is_test": scheme.get("is_test", False), "official_url": scheme.get("official_url"),
        })
    results.sort(key=lambda r: -r["score"])
    for i, r in enumerate(results, 1):
        r["rank"] = i
        why = ", ".join(r["strengths_en"]) or "similar to the other options"
        r["explanation_en"] = f"#{i}: {why}." + (f" Trails on: {', '.join(r['weaknesses_en'])}." if r["weaknesses_en"] else "")
    return {
        "results": results, "excluded": excluded,
        "criteria_used": [c for c, _ in used], "criteria_dropped": dropped,
        "weights": w, "preset": preset,
        "note_en": "Ranked by how well each scheme fits you. This is not a prediction of approval."
                   + (" Amounts were not compared because some are not verified." if "benefit" in dropped else ""),
        "note_ml": "നിങ്ങൾക്ക് എത്രത്തോളം അനുയോജ്യമാണ് എന്നതനുസരിച്ചുള്ള ക്രമം. അംഗീകാരത്തിന്റെ പ്രവചനമല്ല.",
    }
