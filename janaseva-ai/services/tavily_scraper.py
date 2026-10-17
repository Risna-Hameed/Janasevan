"""Find new schemes on official websites with Tavily (owned by P3).

How it works (one click on "Find new schemes online", or `python manage_schemes.py scrape`):
  1. Tavily searches ONLY the official domains below with a few fixed questions and returns each
     matching page (web page or PDF) with its text already extracted (include_raw_content).
  2. Portal home pages and pages that are not about a scheme are skipped; schemes already in the
     catalog are skipped (same link or a very similar name).
  3. From each new notice we copy only what is written in it: income limit, minimum marks, UG/PG,
     girls-only, closing date and the amount sentence - with the sentence each fact came from.
  4. The new schemes are saved to instance/tavily_schemes.json and appear in Discover, search and the
     chatbot, labelled "Found online - needs verification".

Honesty rules: nothing is guessed - a fact not clearly written is left out. Every rule read this way is
stored with verified=False, so the eligibility engine never says "potentially eligible" for these schemes,
and JANASEVA AI sends the student to the official page instead of preparing an application.

Credits: each search costs 1 Tavily credit (basic depth). One click runs len(QUERIES) searches.
The API key comes from TAVILY_API_KEY (environment or the .env file) and never reaches the browser.
"""
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests

from services import retrieval

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))
STORE_PATH = os.path.join(ROOT, "instance", "tavily_schemes.json")
MIN_GAP_SECONDS = 120          # protects the free credits from repeated clicks
_lock = threading.Lock()

OFFICIAL_DOMAINS = ["scholarships.gov.in", "dcescholarship.kerala.gov.in", "minoritywelfare.kerala.gov.in",
                    "egrantz.kerala.gov.in", "aicte-india.org", "aicte.gov.in"]
QUERIES = [
    "Kerala scholarship 2026 notification",
    "scholarship scheme eligibility income limit 2026-27",
    "post matric scholarship guidelines eligibility",
    "merit scholarship for degree students notification",
]

SCHEME_WORDS = re.compile(r"scholarship|scheme|fellowship|stipend|yojana|post[- ]?matric|pre[- ]?matric|"
                          r"financial assistance|grant", re.I)
GENERIC_TITLES = re.compile(r"^\s*(nsp\s*[-–]\s*)?national scholarship portal\b|^home\b|^welcome\b|login|"
                            r"faq|frequently asked|contact us|user manual|institute\b.*registration", re.I)


# ---------------------------------------------------------------- storage
def configure(path):
    """Point the store (and the catalog reader) at a file in the instance folder."""
    global STORE_PATH
    STORE_PATH = path
    retrieval.TAVILY_PATH = path


def load_store():
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"schemes": [], "runs": [], "hidden": []}


def _save(data):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE_PATH)          # atomic - readers never see half a file


def api_key():
    return os.environ.get("TAVILY_API_KEY", "").strip()


# ---------------------------------------------------------------- calling Tavily
def tavily_search(query, key):
    """One Tavily search over the official domains. Uses the tavily-python SDK if installed, else the REST API."""
    params = dict(query=query, search_depth="basic", max_results=5, include_domains=OFFICIAL_DOMAINS,
                  include_raw_content=True)
    try:
        from tavily import TavilyClient
    except ImportError:
        r = requests.post("https://api.tavily.com/search", json=params, timeout=45,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            raise PermissionError("Tavily rejected the API key. Check TAVILY_API_KEY in your .env file.")
        if r.status_code == 432 or r.status_code == 429:
            raise RuntimeError("Tavily credit or rate limit reached. Try again later.")
        r.raise_for_status()
        return r.json()
    return TavilyClient(api_key=key).search(**params)


# ---------------------------------------------------------------- reading a notice
_BOUNDARY = re.compile(r"\n|\.\s+(?=[A-Zഀ-ൿ])")     # "Rs. 2,00,000" is not a sentence end


def _sentence(text, pos):
    start = 0
    for m in _BOUNDARY.finditer(text, 0, pos):
        start = m.end()
    m = _BOUNDARY.search(text, pos)
    end = m.start() + 1 if m else len(text)
    return " ".join(text[start:end].split())[:300]


def _money(num, unit):
    v = float(num.replace(",", ""))
    return int(round(v * 100000)) if unit and unit.lower().startswith(("lakh", "lac")) else int(round(v))


def read_notice(text, state=None):
    """Copy the facts written in the notice. Returns rules (all verified=False) + evidence sentences.
    A fact that is not clearly written is left out - never guessed."""
    t = text or ""
    rules, extra_evidence = [], []
    out = {"deadline": None, "benefit_sentence": None}

    def add(field, op, value, en, ml, pos):
        rules.append({"id": f"t{len(rules) + 1}", "field": field, "operator": op, "value": value, "mandatory": True,
                      "description_en": en + " (read from the official page - confirm there)",
                      "description_ml": ml + " (ഔദ്യോഗിക പേജിൽ നിന്ന് വായിച്ചത് - അവിടെ ഉറപ്പാക്കുക)",
                      "verified": False, "auto_read": True, "source_ref": "found_online",
                      "evidence": _sentence(t, pos) if pos is not None else None})

    if state == "Kerala":
        add("state", "==", "Kerala", "Must be a Kerala resident", "കേരള നിവാസി ആയിരിക്കണം", None)

    m = (re.search(r"income[^.\n]{0,80}?(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?)?", t, re.I)
         or re.search(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?)?[^.\n]{0,40}income", t, re.I))
    if m:
        limit = _money(m.group(1), m.group(2))
        if 10000 <= limit <= 5_000_000:                 # a sensible yearly income limit, not a stray number
            add("annual_income", "<=", limit, f"Annual family income up to ₹{limit:,}",
                f"വാർഷിക കുടുംബ വരുമാനം ₹{limit:,} വരെ", m.start())

    m = (re.search(r"(\d{2}(?:\.\d+)?)\s*%\s*(?:marks|or above|and above|aggregate|or more)", t, re.I)
         or re.search(r"minimum\s+(?:of\s+)?(\d{2}(?:\.\d+)?)\s*%", t, re.I))
    if m and 30 <= float(m.group(1)) <= 100:
        pct = float(m.group(1))
        pct = int(pct) if pct.is_integer() else pct
        add("previous_exam_percentage", ">=", pct, f"At least {pct}% marks in the qualifying examination",
            f"യോഗ്യതാ പരീക്ഷയിൽ കുറഞ്ഞത് {pct}% മാർക്ക്", m.start())

    pg = re.search(r"post ?graduate|\bpg\b|m\.\s?sc|m\.\s?tech|\bmba\b|\bmca\b", t, re.I)
    ug_strict = re.search(r"under ?graduate|b\.\s?tech|b\.\s?sc|b\.\s?com|\bug\b", t, re.I)
    ug = ug_strict or re.search(r"\bdegree\b", t, re.I)
    if pg and not ug_strict:
        add("education_level", "in", ["PG"], "For postgraduate students", "ബിരുദാനന്തര വിദ്യാർത്ഥികൾക്ക്", pg.start())
    elif ug and not pg:
        add("education_level", "in", ["UG"], "For undergraduate (degree) students", "ബിരുദ വിദ്യാർത്ഥികൾക്ക്", ug.start())

    g = re.search(r"\b(girl students?|girls only|only for girls|women students?|female students?)\b", t, re.I)
    if g:
        add("gender", "==", "female", "Only for girl students", "പെൺകുട്ടികൾക്ക് മാത്രം", g.start())

    d = re.search(r"(?:last date|closing date|deadline|due date|apply before)[^0-9\n]{0,60}?"
                  r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4})", t, re.I)
    if d:
        out["deadline"] = d.group(1)
        extra_evidence.append(_sentence(t, d.start()))

    b = re.search(r"[^.\n]*(?:scholarship amount|amount of|per annum|per month|per year)[^.\n]*(?:rs\.?|₹)[^.\n]*"
                  r"|[^.\n]*(?:rs\.?|₹)\s*[\d,]+[^.\n]*(?:per annum|per month|per year)[^.\n]*", t, re.I)
    if b:
        out["benefit_sentence"] = " ".join(b.group(0).split())[:300]
    out["rules"] = rules
    out["evidence"] = [r["evidence"] for r in rules if r.get("evidence")] + extra_evidence
    return out


# ---------------------------------------------------------------- deciding what is a new scheme
def _norm(s):
    s = re.sub(r"\(.*?\)|\b20\d\d(-\d\d)?\b|\.pdf$", " ", (s or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", s))


def scheme_name(title, text):
    """Use the page title, unless it is a file name or too vague - then the first line of the text that names a scheme."""
    t = " ".join((title or "").split())
    if t and SCHEME_WORDS.search(t) and not re.search(r"\.pdf$|^[\w-]+$", t) and not GENERIC_TITLES.search(t):
        return t[:150]
    for line in (text or "").splitlines()[:60]:
        line = " ".join(line.split()).strip(" -:•")
        if 12 <= len(line) <= 140 and SCHEME_WORDS.search(line) and not GENERIC_TITLES.search(line):
            return line
    return None


def looks_like_scheme_page(url, title, text):
    path = urlparse(url).path.strip("/")
    if not path or (path.count("/") == 0 and not path.lower().endswith(".pdf") and "." not in path):
        return False                                   # portal home / section pages such as /Students, /Institute
    if GENERIC_TITLES.search(title or ""):
        return False
    head = (title or "") + " " + (text or "")[:3000]
    return bool(SCHEME_WORDS.search(head)) and bool(re.search(r"eligib|income|apply|applicant|students?", head, re.I))


def find_existing(name, url, records):
    n = _norm(name)
    for r in records:
        if url and (url == r.get("official_url") or url in {x.get("url") for x in r.get("sources", [])}):
            return r["id"]
        rn = _norm(r.get("name_en"))
        if n and rn and (n == rn or SequenceMatcher(None, n, rn).ratio() >= 0.85 or (len(n) > 20 and (n in rn or rn in n))):
            return r["id"]
    return None


def build_record(name, url, text, today):
    host = urlparse(url).netloc.lower()
    state = "Kerala" if "kerala" in host else "All India"
    found = read_notice(text, state)
    words = sorted({w for w in re.findall(r"[a-z]{3,}", name.lower()) if w not in retrieval.STOPWORDS})
    return {
        "id": f"web_{'_'.join(_norm(name).split())[:40]}_{hashlib.sha1(url.encode()).hexdigest()[:6]}",
        "is_test": False,
        "name_en": name, "name_ml": name,
        "department": host.replace("www.", ""),
        "category": "scholarship",
        "state": state,
        "beneficiary_group": ["students"],
        "description_en": (f"Found on {host.replace('www.', '')} on {today} by an online search. The details below were "
                           "copied from that page; confirm them on the official page before applying."),
        "description_ml": (f"{today}-ന് ഓൺലൈൻ തിരച്ചിലിൽ {host.replace('www.', '')}-ൽ കണ്ടെത്തിയത്. താഴെയുള്ള വിവരങ്ങൾ ആ പേജിൽ "
                           "നിന്ന് എടുത്തതാണ്; അപേക്ഷിക്കും മുമ്പ് ഔദ്യോഗിക പേജിൽ ഉറപ്പാക്കുക."),
        "keywords": words + ["scholarship", "new"],
        "eligibility_rules": found["rules"],
        "required_documents": [],
        "benefits": found["benefit_sentence"] or "See the official page",
        "benefit_value_inr": None,                 # ranking only uses verified amounts
        "renewable": None, "mutually_exclusive_with": [],
        "application_method": "Apply on the official portal",
        "official_url": url,
        "deadline": found["deadline"],
        "sources": [{"ref": "found_online", "title": name, "url": url, "type": "official_found_online"}],
        "verification_note": "Found by an online search (Tavily). Rules were copied from the page text and are not yet "
                             "confirmed by a person.",
        "last_verified": None,
        "source_status": "found_online",
        "found": {"on": today, "url": url, "evidence": found["evidence"]},
    }


# ---------------------------------------------------------------- one search run
def scrape(search=None, key=None, today=None, force=False):
    """Run all QUERIES through Tavily and add new schemes. Returns a report dict."""
    key = key if key is not None else api_key()
    if not key and search is None:
        return {"ok": False, "error": "TAVILY_API_KEY is not set. Add it to the .env file and restart the app."}
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "A search is already running."}
    try:
        store = load_store()
        now = datetime.now(timezone.utc)
        last = next((r["at"] for r in store.get("runs", []) if r.get("ok")), None)   # only successful searches count
        if last and not force and (now - datetime.fromisoformat(last)).total_seconds() < MIN_GAP_SECONDS:
            return {"ok": False, "error": "Searched less than 2 minutes ago. Please wait a moment.", "wait": True}
        search = search or (lambda q: tavily_search(q, key))
        today = today or now.astimezone(IST).date().isoformat()
        catalog = retrieval.load_base_schemes() + store["schemes"]
        report = {"ok": True, "at": now.isoformat(), "added": [], "already_known": 0, "skipped": 0, "errors": [],
                  "queries": len(QUERIES)}
        seen = set()
        for q in QUERIES:
            try:
                res = search(q)
            except Exception as e:  # noqa: BLE001 - one failed query must not stop the others
                report["errors"].append(f"{q}: {_friendly(e)}")
                if isinstance(e, (PermissionError, requests.ConnectionError)):
                    report["errors"] = [_friendly(e)] * len(QUERIES)       # same cause for every query - stop here
                    break
                continue
            for r in res.get("results", []):
                url = r.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                host = urlparse(url).netloc.lower()
                if not any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS):
                    report["skipped"] += 1
                    continue
                text = r.get("raw_content") or r.get("content") or ""
                name = scheme_name(r.get("title"), text)
                if not name or not looks_like_scheme_page(url, r.get("title"), text):
                    report["skipped"] += 1
                    continue
                if find_existing(name, url, catalog):
                    report["already_known"] += 1
                    continue
                rec = build_record(name, url, text, today)
                store["schemes"].append(rec)
                catalog.append(rec)
                report["added"].append({"id": rec["id"], "name": name, "url": url, "rules_read": len(rec["eligibility_rules"])})
        if len(report["errors"]) == len(QUERIES):
            report["ok"] = False
            report["error"] = report["errors"][0].split(": ", 1)[-1]
            report["errors"] = [report["error"]]
        store["runs"] = ([{k: report[k] for k in ("at", "added", "already_known", "skipped", "errors", "ok")}]
                         + store.get("runs", []))[:20]
        _save(store)
        return report
    finally:
        _lock.release()


def _friendly(e):
    if isinstance(e, (requests.ConnectionError, ConnectionError)):
        return "Could not reach Tavily. Check the internet connection and try again."
    if isinstance(e, requests.Timeout):
        return "Tavily took too long to answer. Try again."
    return str(e)[:150]


def set_hidden(scheme_id, hidden=True):
    store = load_store()
    if not any(s["id"] == scheme_id for s in store["schemes"]):
        return False
    h = set(store.get("hidden", []))
    h.add(scheme_id) if hidden else h.discard(scheme_id)
    store["hidden"] = sorted(h)
    _save(store)
    return True


def status():
    store = load_store()
    hidden = set(store.get("hidden", []))
    return {"configured": bool(api_key()),
            "last_search": store["runs"][0]["at"] if store.get("runs") else None,
            "last_result": store["runs"][0] if store.get("runs") else None,
            "found": [{"id": s["id"], "name_en": s["name_en"], "found_on": s["found"]["on"], "url": s["official_url"],
                       "deadline": s.get("deadline"), "rules_read": len(s["eligibility_rules"])}
                      for s in reversed(store["schemes"]) if s["id"] not in hidden],
            "domains": OFFICIAL_DOMAINS}
