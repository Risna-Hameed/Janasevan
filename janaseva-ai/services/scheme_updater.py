"""Daily scheme updater - keeps the catalog current without anyone pressing a button.

Once a day (and once at start-up if the last run is older than a day) it:
  1. reads each official listing page in data/scheme_sources.json and finds links that look like schemes;
  2. for every link not already in the catalog, opens the notice and reads the facts written in it
     (income limit, minimum marks, course level, girls-only, closing date, amount sentence);
  3. publishes the new scheme on the site straight away, labelled "New - added automatically";
  4. re-checks every official document listed in schemes.json and flags a scheme whose document changed;
  5. marks auto-added schemes that are no longer listed on their source page.

Honesty rules (same as the rest of JANASEVA AI):
  * nothing is invented - every fact comes from the text of the official page, and the sentence it came
    from is kept as evidence;
  * auto-read rules are stored with verified=False, so the rule engine can say "condition not met" or
    "more information needed" but never "potentially eligible" for an auto-added scheme;
  * applications for auto-added schemes are sent to the official portal (see application_submission.check);
  * only allow-listed domains are fetched, robots.txt is obeyed, requests are spaced out, nothing is
    submitted and no login is attempted.

State lives in two JSON files in the instance folder (auto_schemes.json is read by retrieval.load_schemes).
"""
import hashlib
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests

from services import retrieval

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "data", "scheme_sources.json")
SAMPLES_DIR = os.path.join(ROOT, "data", "samples")
IST = timezone(timedelta(hours=5, minutes=30))

STATE_DIR = os.path.join(ROOT, "instance", "scheme_updates")
_run_lock = threading.Lock()
_scheduler_started = False

MAX_NEW_PER_RUN = 15          # never flood the catalog from one odd page
MISSING_RUNS_TO_HIDE = 3      # an auto-added scheme gone from its source for 3 runs is hidden
RUNS_KEPT = 30

SCHEME_WORDS = re.compile(r"scholarship|scheme|fellowship|stipend|grant|award|incentive|financial assistance|yojana|"
                          r"സ്കോളർഷിപ്പ്|പദ്ധതി|ധനസഹായം", re.I)
SKIP_WORDS = re.compile(r"\b(log ?in|sign ?(in|up)|register|registration|faq|contact|home|about|grievance|track|status|"
                        r"help ?desk|help|sitemap|privacy|terms|disclaimer|feedback|screen reader|skip to|tender|"
                        r"recruitment|rti|gallery|forgot|password|download app|user manual|institute login)\b", re.I)


# ---------------------------------------------------------------- files
def configure(state_dir):
    """Point the updater (and the catalog reader) at the instance folder."""
    global STATE_DIR
    STATE_DIR = state_dir
    os.makedirs(state_dir, exist_ok=True)
    retrieval.AUTO_PATH = os.path.join(state_dir, "auto_schemes.json")


def _path(name):
    return os.path.join(STATE_DIR, name)


def _load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = _path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(name))          # atomic: readers never see half a file


def load_sources():
    with open(SOURCES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- fetching
class _Page(HTMLParser):
    """Collects the title, the visible text and every link (href + text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.links, self.text = "", [], []
        self._skip = 0
        self._in_title = False
        self._href, self._link_text = None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href, self._link_text = dict(attrs).get("href"), []
        elif tag in ("p", "li", "br", "div", "h1", "h2", "h3", "tr", "td"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._link_text).split())))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._link_text.append(data)
        self.text.append(data)


def _clean_url(url):
    p = urlparse(url)
    return urlunparse(p._replace(path=quote(p.path, safe="/%:@-._~!$&'()*+,;="), fragment=""))


class Fetcher:
    """Polite HTTP client: allow-list, robots.txt, a gap between requests to the same host, size cap."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.allowed = [d.lower() for d in cfg.get("allowed_domains", [])]
        self.ua = cfg.get("user_agent", "JanasevaAI-SchemeUpdater/1.0")
        self.gap = float(cfg.get("request_gap_seconds", 2))
        self.timeout = float(cfg.get("timeout_seconds", 15))
        self.max_bytes = int(cfg.get("max_bytes", 4_000_000))
        self._robots, self._last_hit = {}, {}

    def allowed_url(self, url):
        if url.startswith("file:"):
            full = os.path.realpath(os.path.join(ROOT, url[5:]))
            return full.startswith(os.path.realpath(SAMPLES_DIR) + os.sep)
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in self.allowed)

    def _robots_ok(self, url):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = requests.get(base + "/robots.txt", timeout=self.timeout, headers={"User-Agent": self.ua})
                if r.status_code in (401, 403):
                    rp.disallow_all = True
                elif r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp.allow_all = True
            except requests.RequestException:
                rp.allow_all = True       # robots.txt unreachable: same as no robots.txt
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.ua, url)

    def get(self, url):
        """Return (content_type, bytes). Raises ValueError / requests errors on refusal or failure."""
        if not self.allowed_url(url):
            raise ValueError("domain not on the allow-list")
        if url.startswith("file:"):
            path = os.path.join(ROOT, url[5:])
            with open(path, "rb") as f:
                data = f.read(self.max_bytes)
            return ("application/pdf" if path.lower().endswith(".pdf") else "text/html"), data
        url = _clean_url(url)
        if not self._robots_ok(url):
            raise ValueError("robots.txt asks automated readers not to open this page")
        host = urlparse(url).netloc
        wait = self.gap - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()
        with requests.get(url, timeout=self.timeout, stream=True, headers={"User-Agent": self.ua}) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > self.max_bytes:
                    break
            return r.headers.get("Content-Type", ""), data


def page_text(content_type, data):
    """Readable text of an HTML page or a PDF (first 12 pages)."""
    if "pdf" in (content_type or "").lower() or data[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages[:12]), "", []
        except Exception:  # noqa: BLE001 - a broken PDF is just "no text"
            return "", "", []
    html = data.decode("utf-8", errors="replace")
    p = _Page()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        pass
    text = re.sub(r"[ \t]+", " ", "".join(p.text))
    return re.sub(r"\n\s*\n+", "\n", text).strip(), " ".join(p.title.split()), p.links


def fingerprint(text):
    """Hash of the visible text, ignoring spacing and visitor counters / clocks that change on every load."""
    t = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", "", (text or "").lower())
    t = re.sub(r"(visitors?|hits?|page views?)\s*:?\s*[\d,]+", "", t)
    return hashlib.sha256(" ".join(t.split()).encode()).hexdigest()


# ---------------------------------------------------------------- reading a notice
def _money(num, unit):
    v = float(num.replace(",", ""))
    return int(round(v * 100000)) if unit and unit.lower().startswith("lakh") else int(round(v))


_BOUNDARY = re.compile(r"\n|\.\s+(?=[A-Z\u0D00-\u0D7F])")   # "Rs. 2,00,000" is not a sentence end


def _sentence(text, pos):
    """The whole sentence around position pos - kept as evidence for an auto-read fact."""
    start = 0
    for m in _BOUNDARY.finditer(text, 0, pos):
        start = m.end()
    m = _BOUNDARY.search(text, pos)
    end = m.start() + 1 if m else len(text)
    return " ".join(text[start:end].split())[:300]


def read_notice(text, state=None):
    """Pull the facts that are written in the notice. Returns rules (verified=False) plus evidence sentences.
    If a fact is not clearly written, it is simply left out - never guessed."""
    rules, evidence, out = [], [], {"deadline": None, "benefit_sentence": None}
    t = text or ""

    def add(field, op, value, en, ml, pos):
        rules.append({"id": f"a{len(rules) + 1}", "field": field, "operator": op, "value": value, "mandatory": True,
                      "description_en": en + " (read automatically from the official notice - confirm there)",
                      "description_ml": ml + " (ഔദ്യോഗിക അറിയിപ്പിൽ നിന്ന് സ്വയം വായിച്ചത് - അവിടെ ഉറപ്പാക്കുക)",
                      "verified": False, "auto_read": True, "source_ref": "notice",
                      "evidence": _sentence(t, pos) if pos is not None else None})

    if state and state != "All India":
        add("state", "==", state, f"Must be a {state} resident", f"{state} നിവാസി ആയിരിക്കണം", None)

    m = (re.search(r"income[^.\n]{0,80}?(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?)?", t, re.I)
         or re.search(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?)?[^.\n]{0,40}income", t, re.I))
    if m:
        limit = _money(m.group(1), m.group(2))
        if 10000 <= limit <= 5_000_000:          # a sensible yearly income limit, not a stray number
            add("annual_income", "<=", limit, f"Annual family income up to ₹{limit:,}",
                f"വാർഷിക കുടുംബ വരുമാനം ₹{limit:,} വരെ", m.start())

    m = (re.search(r"(\d{2}(?:\.\d+)?)\s*%\s*(?:marks|or above|and above|aggregate|or more)", t, re.I)
         or re.search(r"minimum\s+(?:of\s+)?(\d{2}(?:\.\d+)?)\s*%", t, re.I))
    if m and 30 <= float(m.group(1)) <= 100:
        pct = float(m.group(1))
        pct = int(pct) if pct.is_integer() else pct
        add("previous_exam_percentage", ">=", pct, f"At least {pct}% marks in the qualifying examination",
            f"യോഗ്യതാ പരീക്ഷയിൽ കുറഞ്ഞത് {pct}% മാർക്ക്", m.start())

    pg = re.search(r"post ?graduate|\bpg\b|m\.\s?sc|m\.\s?tech|\bm\.?a\.|\bmba\b|\bmca\b", t, re.I)
    ug = re.search(r"under ?graduate|\bdegree\b|b\.\s?tech|b\.\s?sc|\bb\.?a\.|b\.\s?com|\bug\b", t, re.I)
    if pg and ug and not re.search(r"under ?graduate|b\.\s?tech|b\.\s?sc|\bb\.?a\.|b\.\s?com|\bug\b", t, re.I):
        ug = None          # only the word "degree" (e.g. "marks in the degree examination") - still a PG scheme
    if pg and not ug:
        add("education_level", "in", ["PG"], "For postgraduate students", "ബിരുദാനന്തര വിദ്യാർത്ഥികൾക്ക്", pg.start())
    elif ug and not pg:
        add("education_level", "in", ["UG"], "For undergraduate (degree) students", "ബിരുദ വിദ്യാർത്ഥികൾക്ക്", ug.start())

    g = re.search(r"\b(girls?|women|female)\b", t, re.I)
    if g:
        add("gender", "==", "female", "Only for girl students", "പെൺകുട്ടികൾക്ക് മാത്രം", g.start())

    d = re.search(r"(?:last date|closing date|deadline|due date|apply before)[^0-9\n]{0,60}?"
                  r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4})", t, re.I)
    if d:
        out["deadline"] = d.group(1)
        evidence.append(_sentence(t, d.start()))

    b = re.search(r"[^.\n]*(?:scholarship amount|amount of|per annum|per month|per year)[^.\n]*(?:rs\.?|₹)[^.\n]*"
                  r"|[^.\n]*(?:rs\.?|₹)\s*[\d,]+[^.\n]*(?:per annum|per month|per year)[^.\n]*", t, re.I)
    if b:
        out["benefit_sentence"] = " ".join(b.group(0).split())[:300]
    out["rules"] = rules
    out["evidence"] = [r["evidence"] for r in rules if r.get("evidence")] + evidence
    return out


# ---------------------------------------------------------------- catalog matching
def _norm_name(s):
    s = re.sub(r"\(.*?\)|\b20\d\d(-\d\d)?\b", " ", (s or "").lower())
    return " ".join(re.findall(r"[a-zഀ-ൿ0-9]+", s))


def find_existing(title, url, records):
    """Id of a catalog record that is the same scheme (same link or a very similar name), else None."""
    n = _norm_name(title)
    for r in records:
        urls = {r.get("official_url")} | {x.get("url") for x in r.get("sources", [])}
        if url and url in urls and r.get("source_status") == "auto_added":
            return r["id"]
        rn = _norm_name(r.get("name_en"))
        if n and rn and (n == rn or SequenceMatcher(None, n, rn).ratio() >= 0.85 or (len(n) > 20 and (n in rn or rn in n))):
            return r["id"]
    return None


def _slug(s):
    return "_".join(re.findall(r"[a-z0-9]+", _norm_name(s)))[:40] or "scheme"


def build_record(title, url, source, notice, today):
    found = read_notice(notice or "", source.get("state"))
    words = sorted({w for w in re.findall(r"[a-z]{3,}", title.lower()) if w not in retrieval.STOPWORDS})
    category = "scholarship" if re.search(r"scholarship|fellowship|stipend", title, re.I) else "scheme"
    demo = bool(source.get("is_demo"))
    return {
        "id": f"auto_{_slug(title)}_{hashlib.sha1(url.encode()).hexdigest()[:6]}",
        "is_test": False, "is_demo": demo,
        "name_en": title, "name_ml": title,
        "department": source.get("department") or source.get("name"),
        "category": category, "state": source.get("state") or "All India",
        "beneficiary_group": ["students"],
        "description_en": (f"Added automatically on {today} from {source.get('name')}. The details below were read from "
                           "the official notice; confirm them on the official page before applying."),
        "description_ml": (f"{today}-ന് {source.get('name')}-ൽ നിന്ന് സ്വയം ചേർത്തത്. താഴെയുള്ള വിവരങ്ങൾ ഔദ്യോഗിക "
                           "അറിയിപ്പിൽ നിന്ന് വായിച്ചതാണ്; അപേക്ഷിക്കും മുമ്പ് ഔദ്യോഗിക പേജിൽ ഉറപ്പാക്കുക."),
        "keywords": words + [category, "new"],
        "eligibility_rules": found["rules"],
        "required_documents": [],
        "benefits": found["benefit_sentence"] or "See the official notice",
        "benefit_value_inr": None,                 # ranking only uses verified amounts
        "renewable": None, "mutually_exclusive_with": [],
        "application_method": "Apply on the official portal",
        "official_url": None if url.startswith("file:") else url,
        "deadline": found["deadline"],
        "sources": [{"ref": "notice", "title": title, "url": url, "type": "official_auto_read"}],
        "verification_note": ("Added automatically by the daily updater. Rules were read from the notice text and are "
                               "not yet confirmed by a person." + (" FICTIONAL demo record." if demo else "")),
        "last_verified": None,
        "source_status": "auto_added",
        "auto": {"added_on": today, "source_id": source["id"], "source_name": source.get("name"), "url": url,
                 "last_seen": today, "missing_runs": 0, "notice_read": bool(notice), "evidence": found["evidence"]},
    }


# ---------------------------------------------------------------- the daily run
def run_update(fetcher=None, today=None, only_sources=None):
    """Run one update. Safe to call from the scheduler, the CLI or the admin button (one run at a time)."""
    if not _run_lock.acquire(blocking=False):
        return {"busy": True}
    if not _file_lock():                       # another server process is already updating
        _run_lock.release()
        return {"busy": True}
    try:
        cfg = load_sources()
        fetcher = fetcher or Fetcher(cfg)
        started = _now()
        today = today or started.astimezone(IST).date().isoformat()
        auto = _load("auto_schemes.json", {"schemes": [], "doc_changes": {}})
        state = _load("updater_state.json", {"docs": {}, "runs": []})
        base = retrieval.load_base_schemes()
        report = {"started": started.isoformat(), "added": [], "seen_again": 0, "skipped_known": 0,
                  "changed": [], "unlisted": [], "sources": [], "errors": []}

        # 1-3. listing pages -> new schemes
        new_budget = MAX_NEW_PER_RUN
        for src in cfg.get("sources", []):
            if not src.get("enabled", True) or (only_sources and src["id"] not in only_sources):
                continue
            entry = {"id": src["id"], "name": src.get("name"), "ok": False, "links_checked": 0, "new": 0}
            try:
                ctype, data = fetcher.get(src["url"])
                _, _, links = page_text(ctype, data)
                entry["ok"] = True
            except Exception as e:  # noqa: BLE001 - one broken site must not stop the run
                entry["error"] = str(e)[:200]
                report["errors"].append(f"{src.get('name')}: {entry['error']}")
                report["sources"].append(entry)
                continue
            seen_here = set()
            for href, text in links:
                if not href or href.startswith(("mailto:", "javascript:", "#", "tel:")):
                    continue
                url = urljoin(src["url"], href)
                if (not text or len(text) < 8 or len(text) > 180 or not SCHEME_WORDS.search(text)
                        or SKIP_WORDS.search(text) or url in seen_here):
                    continue
                seen_here.add(url)
                entry["links_checked"] += 1
                known = find_existing(text, url, auto["schemes"]) or find_existing(text, url, base)
                if known:
                    rec = next((r for r in auto["schemes"] if r["id"] == known), None)
                    if rec:
                        rec["auto"].update(last_seen=today, missing_runs=0)
                        report["seen_again"] += 1
                    else:
                        report["skipped_known"] += 1
                    continue
                if new_budget <= 0 or not fetcher.allowed_url(url):
                    continue
                notice = ""
                try:
                    ctype, data = fetcher.get(url)
                    notice, title, _ = page_text(ctype, data)
                except Exception as e:  # noqa: BLE001 - publish the link even if the notice cannot be read
                    report["errors"].append(f"{text[:60]}: notice not readable ({str(e)[:80]})")
                rec = build_record(text, url, src, notice, today)
                auto["schemes"].append(rec)
                report["added"].append({"id": rec["id"], "name": rec["name_en"], "rules_read": len(rec["eligibility_rules"])})
                entry["new"] += 1
                new_budget -= 1
            # 5. auto-added schemes from this source that are no longer listed
            for rec in auto["schemes"]:
                if rec["auto"]["source_id"] == src["id"] and rec["auto"]["url"] not in seen_here \
                        and rec["auto"].get("last_seen") != today:
                    rec["auto"]["missing_runs"] = rec["auto"].get("missing_runs", 0) + 1
                    if rec["auto"]["missing_runs"] >= MISSING_RUNS_TO_HIDE:
                        rec["auto"]["no_longer_listed"] = today
                        report["unlisted"].append(rec["id"])
            report["sources"].append(entry)

        # 4. watch official documents of the curated schemes
        if not only_sources:
            for s in base:
                if s.get("is_test"):
                    continue
                for doc in s.get("sources", []):
                    url = doc.get("url")
                    if not url or not fetcher.allowed_url(url):
                        continue
                    try:
                        ctype, data = fetcher.get(url)
                        text, _, _ = page_text(ctype, data)
                        h = fingerprint(text) if text else hashlib.sha256(data).hexdigest()
                    except Exception as e:  # noqa: BLE001
                        report["errors"].append(f"{s['id']} document: {str(e)[:120]}")
                        continue
                    prev = state["docs"].get(url)
                    if prev and prev["hash"] != h:
                        auto["doc_changes"][s["id"]] = {"changed_on": today, "url": url, "title": doc.get("title")}
                        report["changed"].append(s["id"])
                    state["docs"][url] = {"hash": h, "checked": today, "scheme_id": s["id"]}

        report["finished"] = _now().isoformat()
        auto["last_run"] = report["finished"]
        state["runs"] = ([{k: report[k] for k in ("started", "finished", "added", "changed", "unlisted", "errors",
                                                   "seen_again", "skipped_known", "sources")}] + state["runs"])[:RUNS_KEPT]
        _save("auto_schemes.json", auto)
        _save("updater_state.json", state)
        return report
    finally:
        try:
            os.remove(_path("run.lock"))
        except OSError:
            pass
        _run_lock.release()


def _file_lock():
    """One update at a time across processes (e.g. several gunicorn workers). A lock older than an hour is stale."""
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = _path("run.lock")
    try:
        if time.time() - os.path.getmtime(lock) > 3600:
            os.remove(lock)
    except OSError:
        pass
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


# ---------------------------------------------------------------- status, hide, schedule
def status(update_hour_ist=6):
    auto = _load("auto_schemes.json", {"schemes": [], "doc_changes": {}})
    state = _load("updater_state.json", {"docs": {}, "runs": []})
    actions = admin_actions()
    hidden = set(actions["hidden"])
    last = auto.get("last_run")
    return {
        "last_run": last, "next_run": next_run_time(last, update_hour_ist).isoformat(),
        "running": _run_lock.locked() or os.path.exists(_path("run.lock")),
        "auto_added": [{"id": r["id"], "name_en": r["name_en"], "added_on": r["auto"]["added_on"],
                        "source": r["auto"].get("source_name"), "url": r["auto"]["url"],
                        "rules_read": len(r["eligibility_rules"]), "deadline": r.get("deadline"),
                        "hidden": r["id"] in hidden, "is_demo": r.get("is_demo", False),
                        "no_longer_listed": r["auto"].get("no_longer_listed")} for r in auto["schemes"]],
        "doc_changes": {k: dict(v, dismissed=actions["dismissed"].get(k) == v["changed_on"])
                        for k, v in auto.get("doc_changes", {}).items()},
        "runs": state["runs"][:10],
        "sources": [{"id": s["id"], "name": s["name"], "url": s["url"], "enabled": s.get("enabled", True)}
                    for s in load_sources()["sources"]],
    }


_admin_lock = threading.Lock()


def admin_actions():
    return _load("admin_actions.json", {"hidden": [], "dismissed": {}})


def set_hidden(scheme_id, hidden):
    """Hide a wrongly added scheme from the site (or show it again). Kept apart from the updater's file,
    so it works while an update is running and is never overwritten by one."""
    if not any(r["id"] == scheme_id for r in _load("auto_schemes.json", {"schemes": []})["schemes"]):
        return False
    with _admin_lock:
        a = admin_actions()
        h = set(a["hidden"])
        h.add(scheme_id) if hidden else h.discard(scheme_id)
        a["hidden"] = sorted(h)
        _save("admin_actions.json", a)
    return True


def clear_doc_change(scheme_id):
    """Official has re-read the changed document: stop showing the 'official document changed' notice
    (until the document changes again)."""
    change = _load("auto_schemes.json", {}).get("doc_changes", {}).get(scheme_id)
    if not change:
        return False
    with _admin_lock:
        a = admin_actions()
        a["dismissed"][scheme_id] = change["changed_on"]
        _save("admin_actions.json", a)
    return True


def next_run_time(last_run_iso, hour_ist=6):
    """The next daily slot (hour_ist:00 India time) after the last run; 'now' if it has never run."""
    now = _now()
    if not last_run_iso:
        return now
    last = datetime.fromisoformat(last_run_iso)
    slot = last.astimezone(IST).replace(hour=hour_ist, minute=0, second=0, microsecond=0)
    while slot <= last:
        slot += timedelta(days=1)
    return slot.astimezone(timezone.utc)


def start_scheduler(hour_ist=6, logger=None):
    """Background thread: runs once a day at hour_ist (and straight away if a run was missed)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        time.sleep(5)                                  # let the web server start first
        while True:
            try:
                last = _load("auto_schemes.json", {}).get("last_run")
                if _now() >= next_run_time(last, hour_ist):
                    r = run_update()
                    if logger and not r.get("busy"):
                        logger.info("Scheme update: %d new, %d changed, %d errors",
                                    len(r["added"]), len(r["changed"]), len(r["errors"]))
            except Exception as e:  # noqa: BLE001 - the scheduler must never die
                if logger:
                    logger.warning("Scheme update failed: %s", e)
            time.sleep(60)

    threading.Thread(target=loop, name="scheme-updater", daemon=True).start()
