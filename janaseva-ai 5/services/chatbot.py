"""JANASEVA AI chatbot (owned by P3).

Design rule: FACTS come from scheme records, ELIGIBILITY comes from P1's
rule engine, and the LLM (if configured) only rewrites the explanation in
simple language. With no API key the template fallback answers instead,
so the app always works.

Response shape (API_CONTRACT.md):
  {facts: [...], explanation: str, sources: [...], follow_up: {...}|None,
   intent: str, lang: "en"|"ml", mode: "llm"|"fallback"}
"""
import os
import re

import requests

from services import retrieval

INTENT_WORDS = {
    "documents": ["document", "documents", "certificate", "papers", "രേഖ", "രേഖകൾ", "സർട്ടിഫിക്കറ്റ്", "docs"],
    "eligibility": ["eligible", "eligibility", "qualify", "why not", "not eligible", "യോഗ്യ", "യോഗ്യത", "അർഹ"],
    "apply": ["apply", "application", "how to apply", "register", "അപേക്ഷ", "അപേക്ഷിക്ക"],
    "recommend": ["scholarship", "scholarships", "scheme", "schemes", "benefit", "benefits", "available",
                  "സ്കോളർഷിപ്പ്", "പദ്ധതി", "ലഭിക്കാവുന്ന", "ആനുകൂല്യ"],
    "greeting": ["hi", "hello", "hey", "namaskaram", "നമസ്കാരം", "ഹലോ"],
}

FOLLOW_UP_FIELDS = ["education_level", "annual_income", "state"]

TEXT = {
    "en": {
        "greeting": "Hello! I'm JANASEVA AI. Ask me about scholarships and government schemes, the documents you need, or how to apply.",
        "unrelated": "I can only help with government schemes and scholarships. Try: 'Which scholarships are available for B.Tech students?'",
        "no_match": "I couldn't find a scheme in my verified records that matches your question. Try different words, or browse the Discover section.",
        "ask_profile": "To suggest suitable schemes, please tell me: your education level, annual family income and state. You can fill the profile form, or just type it here.",
        "found": "I found {n} scheme(s) that may be relevant. Each one is listed below with its source. A match is not proof of eligibility - use 'Check eligibility' for an explained result.",
        "documents": "Here are the documents listed for {name}. Check the official source, because requirements can change.",
        "documents_missing": "The document list for {name} has not been verified yet. Please check the official source.",
        "apply": "How to apply for {name}: {method}. Always submit through the official department process.",
        "elig_result": "Eligibility check for {name}: {status}. This is based only on the information you gave and must be confirmed officially.",
        "elig_use_tool": "To check eligibility for {name}, open it and press 'Check eligibility'. The rule engine will explain each condition.",
        "which_scheme": "Which scheme do you mean? Please choose one from the list.",
        "disclaimer": "JANASEVA AI is an independent prototype. It does not guarantee eligibility or approval.",
    },
    "ml": {
        "greeting": "നമസ്കാരം! ഞാൻ ജനസേവ AI. സ്കോളർഷിപ്പുകൾ, സർക്കാർ പദ്ധതികൾ, ആവശ്യമായ രേഖകൾ, അപേക്ഷിക്കേണ്ട വിധം എന്നിവയെക്കുറിച്ച് ചോദിക്കാം.",
        "unrelated": "സർക്കാർ പദ്ധതികളെയും സ്കോളർഷിപ്പുകളെയും കുറിച്ച് മാത്രമേ എനിക്ക് സഹായിക്കാൻ കഴിയൂ. ഉദാ: 'ബിടെക് വിദ്യാർത്ഥികൾക്കുള്ള സ്കോളർഷിപ്പുകൾ ഏതൊക്കെ?'",
        "no_match": "നിങ്ങളുടെ ചോദ്യവുമായി പൊരുത്തപ്പെടുന്ന പദ്ധതി പരിശോധിച്ച രേഖകളിൽ കണ്ടെത്താനായില്ല. മറ്റ് വാക്കുകൾ ഉപയോഗിച്ച് ശ്രമിക്കുക.",
        "ask_profile": "അനുയോജ്യമായ പദ്ധതികൾ നിർദ്ദേശിക്കാൻ, നിങ്ങളുടെ വിദ്യാഭ്യാസ നില, വാർഷിക കുടുംബ വരുമാനം, സംസ്ഥാനം എന്നിവ പറയുക.",
        "found": "ബന്ധപ്പെട്ടേക്കാവുന്ന {n} പദ്ധതി(കൾ) കണ്ടെത്തി. ഇത് യോഗ്യതയുടെ തെളിവല്ല - വിശദമായ ഫലത്തിന് 'യോഗ്യത പരിശോധിക്കുക' ഉപയോഗിക്കുക.",
        "documents": "{name} പദ്ധതിക്ക് ആവശ്യമായ രേഖകൾ താഴെ നൽകുന്നു. നിബന്ധനകൾ മാറാമെന്നതിനാൽ ഔദ്യോഗിക സ്രോതസ്സ് പരിശോധിക്കുക.",
        "documents_missing": "{name} പദ്ധതിയുടെ രേഖകളുടെ പട്ടിക ഇതുവരെ പരിശോധിച്ചിട്ടില്ല. ഔദ്യോഗിക സ്രോതസ്സ് കാണുക.",
        "apply": "{name} പദ്ധതിക്ക് അപേക്ഷിക്കേണ്ട വിധം: {method}. ഔദ്യോഗിക വകുപ്പ് വഴി മാത്രം അപേക്ഷിക്കുക.",
        "elig_result": "{name}: {status}. നിങ്ങൾ നൽകിയ വിവരങ്ങൾ മാത്രം അടിസ്ഥാനമാക്കിയുള്ളതാണ്; ഔദ്യോഗികമായി സ്ഥിരീകരിക്കുക.",
        "elig_use_tool": "{name} പദ്ധതിയുടെ യോഗ്യത അറിയാൻ, അത് തുറന്ന് 'യോഗ്യത പരിശോധിക്കുക' അമർത്തുക.",
        "which_scheme": "ഏത് പദ്ധതിയെക്കുറിച്ചാണ് ചോദിക്കുന്നത്? പട്ടികയിൽ നിന്ന് ഒന്ന് തിരഞ്ഞെടുക്കുക.",
        "disclaimer": "ജനസേവ AI ഒരു സ്വതന്ത്ര മാതൃകയാണ്. യോഗ്യതയോ അംഗീകാരമോ ഉറപ്പ് നൽകുന്നില്ല.",
    },
}


def detect_language(message, requested=None):
    if requested in ("en", "ml"):
        return requested
    return "ml" if re.search(r"[ഀ-ൿ]", message or "") else "en"


def detect_intent(message):
    m = (message or "").lower()
    words = set(re.findall(r"[a-z]+|[ഀ-ൿ]+", m))
    for intent in ("documents", "eligibility", "apply", "recommend"):
        if any((w in m) if (" " in w or re.match(r"[ഀ-ൿ]", w)) else (w in words)
               for w in INTENT_WORDS[intent]):
            return intent
    if words & set(INTENT_WORDS["greeting"]) and len(words) <= 3:
        return "greeting"
    return "unknown"


def _missing_profile_fields(profile):
    profile = profile or {}
    return [f for f in FOLLOW_UP_FIELDS if profile.get(f) in (None, "")]


def _try_rule_engine(profile, scheme):
    """Ask P1's rule engine. The chatbot itself NEVER decides eligibility."""
    try:
        from services.eligibility_engine import check_eligibility  # P1's module
    except ImportError:
        return None
    try:
        return check_eligibility(profile or {}, scheme)
    except Exception:  # never let the engine crash the chat
        return None


def _llm_explain(question, facts, draft, lang):
    """Rewrite the template explanation in simple words. Returns None on any failure."""
    key = os.environ.get("LLM_API_KEY", "").strip()
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if not key or provider == "none":
        return None
    language = "simple Malayalam" if lang == "ml" else "simple English"
    system = (
        "You are JANASEVA AI, a helper for Indian government schemes. Write 2-4 short sentences in "
        f"{language} for a citizen with limited technical knowledge. Use ONLY the FACTS given. Never add "
        "amounts, deadlines, conditions, URLs or promises that are not in the FACTS. Never say the person "
        "is definitely eligible or will be approved. If a fact says 'Needs verification', tell them to check "
        "the official source. Do not decide eligibility yourself."
    )
    user = f"QUESTION: {question}\nFACTS: {facts}\nDRAFT ANSWER: {draft}\nRewrite the draft answer clearly."
    try:
        if provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": os.environ.get("LLM_MODEL", "claude-haiku-5-5"), "max_tokens": 400,
                      "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=20,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", [])).strip() or None
        if provider == "gemini":
            model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"parts": [{"text": user}]}]},
                timeout=20,
            )
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip() or None
    except Exception:
        return None  # fall back silently; never expose the key or the error to the user
    return None


def _sources(schemes):
    return [{"scheme_id": s["id"], "name": s.get("name_en"), "url": s.get("official_url"),
             "is_test": s.get("is_test", False), "freshness": retrieval.freshness(s)} for s in schemes]


def answer(message, lang=None, profile=None, scheme_id=None):
    """Main entry point used by POST /api/chat."""
    message = (message or "").strip()
    lang = detect_language(message, lang)
    t = TEXT[lang]
    base = {"facts": [], "sources": [], "follow_up": None, "lang": lang, "mode": "fallback",
            "disclaimer": t["disclaimer"]}

    if not message:
        return {**base, "intent": "empty", "explanation": t["greeting"]}

    intent = detect_intent(message)
    if intent == "greeting":
        return {**base, "intent": intent, "explanation": t["greeting"]}

    # Which schemes is the question about?
    if scheme_id and retrieval.get_scheme(scheme_id):
        hits = [retrieval.get_scheme(scheme_id)]
    else:
        hits = [s for _, s in retrieval.search(message, top_k=4)]

    if intent == "unknown":
        if not hits:
            return {**base, "intent": "unrelated", "explanation": t["unrelated"]}
        intent = "details"

    if intent == "recommend":
        missing = _missing_profile_fields(profile)
        if missing and not scheme_id:
            return {**base, "intent": intent, "explanation": t["ask_profile"],
                    "follow_up": {"fields": missing, "question": t["ask_profile"]}}
        # With a profile, recommend from the BestFit ranking (rules decide), not from keywords
        try:
            from services import ranker
            ranked = [retrieval.get_scheme(r["scheme_id"]) for r in ranker.rank(profile or {})["results"][:4]]
            hits = [s for s in ranked if s] or hits
        except Exception:
            pass
        if not hits:
            hits = retrieval.load_schemes()[:4]
        facts = [retrieval.scheme_facts(s, lang) for s in hits]
        draft = t["found"].format(n=len(hits))
    elif not hits:
        return {**base, "intent": intent, "explanation": t["no_match"]}
    else:
        scheme = hits[0]
        name = scheme.get("name_ml" if lang == "ml" else "name_en")
        facts = [retrieval.scheme_facts(scheme, lang)]
        hits = [scheme]
        if intent == "documents":
            draft = t["documents" if scheme.get("required_documents") else "documents_missing"].format(name=name)
        elif intent == "apply":
            draft = t["apply"].format(name=name, method=facts[0]["application_method"])
        elif intent == "eligibility":
            result = _try_rule_engine(profile, scheme)
            if result:
                facts[0]["eligibility"] = result
                draft = t["elig_result"].format(name=name, status=result.get("status", ""))
            else:
                draft = t["elig_use_tool"].format(name=name)
        else:
            draft = facts[0]["description"]

    explanation = _llm_explain(message, facts, draft, lang)
    return {**base, "intent": intent, "facts": facts, "sources": _sources(hits),
            "explanation": explanation or draft, "mode": "llm" if explanation else "fallback"}
