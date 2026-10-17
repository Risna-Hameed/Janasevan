"""Keyword retrieval over the verified scheme knowledge base (owned by P3).

No embeddings: a small, transparent keyword scorer that understands
English, Malayalam and mixed (Manglish) words. Scheme records are the
only authoritative source; user text is never treated as a rule.
"""
import json
import os
import re
from datetime import date

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "schemes.json")

# Malayalam / Manglish words mapped to English search terms
ML_SYNONYMS = {
    "സ്കോളർഷിപ്പ്": "scholarship", "സ്കോളർഷിപ്പുകൾ": "scholarship", "ധനസഹായം": "scholarship",
    "സ്റ്റൈപ്പൻഡ്": "scholarship", "scholarshipukal": "scholarship", "scholarshippu": "scholarship",
    "ബിരുദം": "ug", "ഡിഗ്രി": "degree", "ബിടെക്": "btech", "എഞ്ചിനീയറിംഗ്": "engineering",
    "ബിരുദാനന്തര": "pg", "പെൺകുട്ടികൾ": "girls", "സ്ത്രീകൾ": "women", "വിദ്യാർത്ഥി": "student",
    "പദ്ധതി": "scheme", "പദ്ധതികൾ": "scheme", "padhathi": "scheme",
}

STOPWORDS = {"the", "a", "an", "for", "of", "to", "is", "am", "i", "my", "me", "can", "what",
             "which", "are", "do", "how", "in", "on", "and", "or", "with", "this", "that", "apply",
             "who", "whom", "whose", "where", "when", "why", "was", "were", "will", "did", "does", "has",
             "have", "be", "it", "you", "your", "get", "give", "tell", "about", "please", "any", "there"}


def load_schemes(include_tests=True):
    """Load scheme records. Returns [] instead of crashing if the file is missing or broken."""
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            schemes = json.load(f).get("schemes", [])
    except (OSError, ValueError):
        return []
    return [s for s in schemes if include_tests or not s.get("is_test")]


def get_scheme(scheme_id):
    return next((s for s in load_schemes() if s.get("id") == scheme_id), None)


def tokenize(text):
    text = (text or "").lower().replace(".", "")
    tokens = re.findall(r"[a-z0-9\-]+|[ഀ-ൿ]+", text)
    out = []
    for t in tokens:
        if t in ML_SYNONYMS:
            out.append(ML_SYNONYMS[t])
        elif t not in STOPWORDS:
            # simple plural handling: "scholarships" -> "scholarship"
            out.append(t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t)
        # Malayalam words carry suffixes (e.g. സ്കോളർഷിപ്പുകൾ); match by prefix
        if re.match(r"[ഀ-ൿ]", t):
            for ml, en in ML_SYNONYMS.items():
                if t.startswith(ml[:5]) and en not in out:
                    out.append(en)
    return out


def search(query, top_k=5, include_tests=True):
    """Return [(score, scheme)] ranked by keyword overlap. Empty query returns all schemes."""
    schemes = load_schemes(include_tests)
    terms = tokenize(query)
    if not terms:
        return [(0, s) for s in schemes[:top_k]]
    results = []
    for s in schemes:
        text = " ".join([
            s.get("name_en", ""), s.get("description_en", ""), s.get("category", ""),
            " ".join(s.get("keywords", [])), " ".join(s.get("beneficiary_group", [])),
        ]).lower().replace(".", "")
        words = set(re.findall(r"[a-z0-9+\-]+", text))
        keywords = {k.lower().replace(".", "") for k in s.get("keywords", [])}
        keyword_words = {w for k in keywords for w in k.split()}
        # whole-word matching only (substring matching made "won" match "government")
        score = sum(3 if t in keywords or t in keyword_words else (1 if t in words else 0) for t in terms)
        if score > 0:
            results.append((score, s))
    results.sort(key=lambda r: (-r[0], r[1].get("is_test", False)))
    return results[:top_k]


def freshness(scheme, today=None):
    """Source freshness badge: green / amber / red. Drives the trust badge in the UI."""
    if scheme.get("source_status") == "test":
        return {"level": "test", "label_en": "Fictional test record", "label_ml": "സാങ്കൽപ്പിക ടെസ്റ്റ് രേഖ"}
    lv = scheme.get("last_verified")
    if scheme.get("source_status") != "verified" or not lv:
        return {"level": "red", "label_en": "Needs verification", "label_ml": "പരിശോധന ആവശ്യമാണ്"}
    try:
        age = ((today or date.today()) - date.fromisoformat(lv)).days
    except ValueError:
        return {"level": "red", "label_en": "Needs verification", "label_ml": "പരിശോധന ആവശ്യമാണ്"}
    if age > 180:
        return {"level": "amber", "label_en": f"Verified {age} days ago - recheck before applying",
                "label_ml": f"{age} ദിവസം മുമ്പ് പരിശോധിച്ചത് - അപേക്ഷിക്കും മുമ്പ് വീണ്ടും പരിശോധിക്കുക"}
    return {"level": "green", "label_en": f"Verified on {lv}", "label_ml": f"{lv}-ന് പരിശോധിച്ചത്"}


def scheme_facts(scheme, lang="en"):
    """Facts copied straight from the record - shown in the 'From verified record' box."""
    ml = lang == "ml"
    nv = "പരിശോധന ആവശ്യമാണ്" if ml else "Needs verification"
    docs = [d.get("name_ml" if ml else "name_en") for d in scheme.get("required_documents", [])]
    return {
        "scheme_id": scheme["id"],
        "name": scheme.get("name_ml" if ml else "name_en"),
        "department": scheme.get("department") or nv,
        "description": scheme.get("description_ml" if ml else "description_en"),
        "conditions": [r.get("description_ml" if ml else "description_en") for r in scheme.get("eligibility_rules", [])] or [nv],
        "documents": docs or [nv],
        "benefits": scheme.get("benefits") or nv,
        "application_method": scheme.get("application_method") or nv,
        "deadline": scheme.get("deadline") or (("അവസാന തീയതി ലഭ്യമല്ല - ഔദ്യോഗികമായി സ്ഥിരീകരിക്കുക") if ml else "No verified deadline - confirm officially"),
        "official_url": scheme.get("official_url"),
        "is_test": scheme.get("is_test", False),
        "freshness": freshness(scheme),
    }
