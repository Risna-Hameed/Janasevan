# JANASEVA AI

An explainable, multilingual (Malayalam / English) assistant for Kerala-relevant government
scholarships and schemes. Hackathon prototype.

> JANASEVA AI is an independent assistance prototype. Scheme information must be verified through
> the relevant official department. This platform does not guarantee eligibility or approval.

## What you get when you run it

A **website** at http://127.0.0.1:5000 with:

| Page | What it does |
|---|---|
| Home | Hero, features, how it works, language switch, text-size button |
| My Profile | Form (only needed fields; optional sensitive fields need a consent tick) |
| Discover Schemes | **Best for you** ranking (BestFit/TOPSIS) with a priority switch, plus search and scheme cards |
| BenefitIQ | Eligibility (met / not met / unknown + "what would change my result"), readiness ring, approval probability "unavailable", document checklist, income-certificate OCR with mismatch check |
| Apply | Draft built only from your data, missing fields highlighted, Akshaya packet PDF, and **Submit application**: pre-submission check (names each missing field, document or failed eligibility condition) → application preview PDF → "I need to change something" (note + edits → new version, preview again) → confirm → **final application PDF** to view and download, with an "Application submitted (demo)" confirmation |
| My Dashboard | Saved schemes and drafts |
| AI Assistant | Chat panel: Malayalam/English, voice input, read-aloud, "From verified record" vs "Explanation" |
| Document Verification | Link your household, send a document to **your** panchayat, track status, upload corrections, download the verification record |

And a separate **Panchayat Dashboard** for officials at **http://127.0.0.1:5000/panchayat**:

| Section | What it does |
|---|---|
| Overview | Live counts (households, people, wards, new / awaiting / correction / verified / referred / overdue requests), recent requests, items needing attention, activity log, 4 charts |
| Verification Requests | Search, filter (ward, category, status, dates), sort, paginate; open the **review workspace** |
| Review workspace | Applicant + document details, secure document viewer, AI pre-check (advice only), checklist, remarks, actions: verify / request documents / refer / unsuccessful / save draft |
| Household Records | Summary list; details on demand; income and assistance needs only for permitted roles (logged) |
| Survey Data | Digitise paper surveys: draft → submit → validate or return; original values kept, corrections logged, duplicate detection |
| Ward Analytics | Aggregates only; survey responses kept separate from preliminary flags |
| Verification History | Every status change, by whom, when |
| Notifications | New and resubmitted requests |
| Administration | Officials, roles, ward access, deactivate, licence status, audit log |

## Run it (Windows / Mac / Linux)

```bash
# 1. get the code, then inside the janaseva-ai folder:
python -m venv venv
venv\Scripts\activate            # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt

# 2. (optional) AI explanations - copy .env.example to .env and add LLM_API_KEY
#    Without a key the app still works using built-in answers.

# 3. start
python app.py
# open http://127.0.0.1:5000
```

**OCR (optional):** install Tesseract. Windows: "Tesseract installer UB Mannheim" (tick Malayalam,
add `C:\Program Files\Tesseract-OCR` to PATH). Mac: `brew install tesseract tesseract-lang`.
Without Tesseract the upload still works and asks for manual entry.

## Panchayat Dashboard: quick demo

The first `python app.py` creates the database (versioned migrations) and loads **fictional** demo
data: 3 grama panchayats in one demo block, wards, households, surveys and requests in every state.

| Sign in at /panchayat as | Role | Shows |
|---|---|---|
| `thanal.officer` | Verification Officer | review and verify requests |
| `thanal.admin` | Panchayat Administrator | everything incl. users, audit, sensitive details |
| `thanal.ward2` | Verification Officer, ward 2 only | ward-level scope |
| `thanal.entry` | Survey Data Entry Operator | survey entry, no verification |
| `thanal.viewer` | Read-Only Official | view only |
| `nila.officer` | Officer of another panchayat | cannot see Thanal's data |
| `puzha.admin` | Panchayat with a **suspended licence** | sign-in refused |

Password for every demo account: **`Demo@2026pass`** (demo only - not government credentials).

**Citizen side:** in the student portal open **Document Verification**, link household
`HH-01-W1-0001` with code `LINK-1001`, upload `data/samples/sample_income_certificate.png`
as an income certificate. Then sign in as `thanal.officer`, open the request, **Start review**,
**Run AI pre-check**, tick the checklist and **Mark as verified**. Back in the student portal the status
changes (it refreshes every 30 s) and the verification record PDF can be downloaded.
Use `sample_income_certificate_mismatch.png` to see the AI flag a name/income mismatch.

### Manage demo data safely

```bash
python manage_demo.py accounts                 # list demo sign-ins
python manage_demo.py reset                    # delete ONLY demo panchayat data, re-create it
python manage_demo.py licence PUZHA active     # active | suspended | expired
SEED_DEMO=0 python app.py                      # start without demo data
```
Reset never touches student profiles, drafts or saved schemes.

**Demo files:** `data/samples/sample_income_certificate.png` (matches the demo profile) and
`sample_income_certificate_mismatch.png` (shows the name/income mismatch alert).

## Daily scheme updates (automatic)

The scheme list keeps itself up to date. While the app is running it checks the official pages
**once a day at 6 am India time** (and right away if a day was missed), with no button to press:

1. **New schemes.** It reads the official listing pages in `data/scheme_sources.json` (NSP, AICTE,
   Kerala Minority Welfare, Kerala DCE scholarships, e-Grantz) and finds links that look like schemes.
   Anything not already in the list is **published on the site straight away** with a blue **New** badge.
2. **Facts from the notice.** It opens each new notice (web page or PDF) and reads only what is written
   there: income limit, minimum marks, UG/PG, girls-only, closing date and the amount sentence. The exact
   sentence each fact came from is shown under "Where these details came from". If a fact is not clearly
   written, it is left out, never guessed.
3. **Changed documents.** Every official document listed for an existing scheme is re-read. If it changed,
   the scheme shows "Official document changed on … - recheck" until an official marks it re-read.
4. **Removed schemes.** A scheme that disappears from its source page for 3 days is taken off the site.

Honesty limits: auto-read rules are stored as *unverified*, so an auto-added scheme can show "Condition
not met" or "Verification required", but **never "Potentially eligible"**. JANASEVA AI does not prepare an
application for it; the student is sent to the official notice instead. Amounts read from notices are shown
as text, but they are not used for ranking.

Politeness: the updater fetches only allow-listed government domains, obeys robots.txt, waits 2 s between
requests, never logs in and never submits anything.

**See it working offline (for the demo):** a fictional source, *Demo Department of Student Welfare*, is
included (`data/samples/demo_feed/`). On the first start it adds two clearly fictional schemes. You can
also run it by hand:

```bash
python manage_schemes.py update demo_feed   # offline demo source only
python manage_schemes.py update             # every official source (needs internet)
python manage_schemes.py status
python manage_schemes.py hide <scheme_id>   # take a wrongly added scheme off the site
```
Officials: sign in as `thanal.admin` → **Scheme Updates** to see what was added, press **Check now**,
hide a wrong entry, or mark a changed document as re-read. Students see a "Scheme list updates
automatically every day" strip at the top of **Discover**.

Before the real demo, open each URL in `data/scheme_sources.json` and confirm it still lists schemes.
Sites built entirely with JavaScript (links drawn after the page loads) show few or no links to a
simple reader. That source is then reported as "0 new", not as an error.

## Run the tests

```bash
python tests/test_p3.py            # chatbot, OCR, packet           (11)
python tests/test_engine_ranker.py # eligibility + BestFit          (11)
python tests/test_panchayat.py     # dashboard, workflow, security  (20)
python tests/test_application_submit.py  # submit flow                (5)
python tests/test_scheme_updater.py      # daily scheme updates       (9)
```
Every test uses a temporary database and file folder - never `instance/janaseva.db`.

## Deploy (Render)

New Web Service → connect repo → runtime **Docker**. Add `LLM_PROVIDER`, `LLM_API_KEY`,
`SECRET_KEY` and `SESSION_COOKIE_SECURE=1` as environment variables. The Dockerfile installs
Tesseract (English + Malayalam). Note: Render's free disk is temporary, so SQLite data and uploaded
documents are lost on redeploy - fine for a demo, not for real use.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | random, saved in `instance/secret_key` | signs official session cookies |
| `DATABASE_PATH` | `instance/janaseva.db` | SQLite file |
| `PRIVATE_UPLOAD_DIR` | `instance/private_documents` | uploaded documents (never under /static) |
| `SEED_DEMO` | `1` | load fictional demo panchayats on first start |
| `PROCESSING_TARGET_DAYS` | `7` | requests older than this are flagged overdue |
| `SESSION_COOKIE_SECURE` | `0` | set `1` when served over HTTPS |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | none | optional chatbot wording |
| `SCHEME_AUTO_UPDATE` | `1` | daily automatic scheme update while the app runs (`0` = off) |
| `SCHEME_UPDATE_HOUR_IST` | `6` | hour of the daily check, India time |
| `SCHEME_UPDATE_DIR` | `instance/scheme_updates` | auto-added schemes, document hashes, run log |

## Project structure and owners

```
app.py, config.py, requirements.txt, .env.example        shared (change together)
database/db.py                                           P1  SQLite (built-in sqlite3)
routes/scheme_routes.py, routes/application_routes.py    P1  schemes, profile, readiness, drafts, saved
services/readiness_engine.py, application_service.py     P1
templates/index.html, static/css, static/js, static/i18n P2  website, Malayalam/English, voice
data/schemes.json, data/VERIFICATION_CHECKLIST.csv       P3  knowledge base + what to verify
services/retrieval.py, chatbot.py                        P3  search (RAG retrieval) + chatbot
services/eligibility_engine.py, ranker.py                P3  rules engine + BestFit ranking
services/document_service.py, packet_service.py          P3  OCR, mismatch check, Akshaya PDF
routes/chat_routes.py, document_routes.py, recommend_routes.py   P3
Dockerfile, README.md                                    P3

Panchayat module (added)
database/migrations.py, db_schema_student.py, seed_panchayat.py   versioned schema + fictional seed
services/panchayat_auth.py         sign-in, roles, jurisdiction scope, licence, CSRF, audit
services/verification_workflow.py  categories, state machine, notifications
services/private_files.py          private document storage (type checked by file bytes)
services/verification_receipt.py   verification record PDF (not a certificate)
routes/panchayat_routes.py         dashboard, requests, review actions, AI pre-check, documents
routes/panchayat_records_routes.py households, surveys, analytics, administration
routes/citizen_verification_routes.py   citizen link, submit, resubmit, status, receipt
templates/panchayat.html, static/css/panchayat.css, static/js/panchayat.js
manage_demo.py, tests/test_panchayat.py
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | status, OCR available, LLM configured |
| GET | /api/schemes?q=&category=&verified_only=1 | list / search schemes |
| GET | /api/schemes/<id> | one scheme |
| GET/POST/DELETE | /api/profile | read / save / clear profile (header `X-Client-Id`) |
| POST | /api/eligibility/check | `{profile, scheme_id}` → explained status |
| POST | /api/eligibility/match | `{profile}` → all schemes with status |
| POST | /api/recommend/best | `{profile, provided_docs, preset}` → ranked list |
| POST | /api/application/readiness | `{scheme_id, provided_items, profile}` → score |
| POST | /api/application/draft | create / update draft |
| POST | /api/application/confirm | demo confirmation only |
| GET | /api/application, /api/application/<id> | drafts |
| POST | /api/application/<id>/check | what blocks submission: missing fields, documents, eligibility |
| GET | /api/application/<id>/pdf?kind=preview\|final&download=1 | application PDF (preview before submit, final after) |
| POST | /api/application/<id>/submit | `{confirmed: true, profile}` - demo submission; needs no blockers + latest preview viewed |
| GET/POST | /api/saved | saved schemes |
| POST | /api/chat | `{message, lang, profile, scheme_id}` |
| POST | /api/documents/analyze | multipart image → fields + mismatches |
| POST | /api/documents/consistency | re-check after edits |
| POST | /api/packet | Akshaya packet PDF |

**Citizen verification** (`X-Client-Id` header): `GET/POST/DELETE /api/citizen/link`,
`GET /api/citizen/document-categories`, `GET/POST /api/citizen/verification-requests`,
`GET /api/citizen/verification-requests/<id>`, `POST …/<id>/resubmit`, `POST …/<id>/cancel`,
`GET …/<id>/receipt`, `GET /api/citizen/notifications`, `POST /api/citizen/notifications/read-all`.

**Panchayat** (signed-in session; writes need `X-CSRF-Token`; all filtered to the official's jurisdiction):

| Method | Path |
|---|---|
| POST | /api/panchayat/login, /logout · GET /me |
| GET | /api/panchayat/dashboard, /analytics, /history, /notifications |
| GET | /api/panchayat/verification-requests (q, ward, category, status, from, to, sort, page) |
| GET | /api/panchayat/verification-requests/<id> · /<id>/ai-review · /<id>/receipt |
| POST | /api/panchayat/verification-requests/<id>/start-review · /review (draft) · /verify · /request-correction · /unsuccessful · /refer |
| GET | /api/panchayat/documents/<id>/preview (`?download=1` for permitted roles) |
| GET | /api/panchayat/households, /households/<id> (`?sensitive=1`) · PATCH /households/<id> |
| GET/POST | /api/panchayat/surveys · GET/PATCH /surveys/<id> · POST /surveys/<id>/submit · /validate |
| GET/POST | /api/panchayat/admin/officials · POST /admin/officials/<id>/assignments · /active · GET /admin/licence · /admin/audit |

### Verification workflow

```
submitted ─(system routes to household's panchayat)→ queued_for_review ─(official starts)→ under_review
under_review → verified | unsuccessful | referred | info_required
info_required ─(citizen uploads more)→ queued_for_review        submitted/queued/info_required → cancelled (citizen)
```
Any other move is rejected (HTTP 409). Community and non-creamy-layer certificates can only be
referred: the panchayat cannot record them as verified. **Verification never approves a scholarship.**

## Limitations (say these honestly in the demo)

- Only one real scheme (Central Sector Scheme) is fully verified; others are marked
  "Needs verification" until the team checks the official pages (see `data/VERIFICATION_CHECKLIST.csv`).
- Auto-added schemes are read by simple text patterns from official notices. They can miss or misread
  a condition, so they are labelled "New · added automatically · confirm on official page", are never shown as
  "Potentially eligible" and cannot be applied for inside the app. Malayalam names are not translated.
- `[TEST]` schemes are fictional and exist only to demonstrate pass / fail cases.
- No approval probability: no validated historical outcome data exists.
- OCR supports income certificates only; reading text does not prove a document is genuine.
- The Akshaya packet PDF is in English (built-in PDF fonts cannot shape Malayalam).
- Submission is a demo; nothing is sent to any government system. DigiLocker is not integrated.
- Voice input depends on the browser (best in Chrome / Edge); Malayalam read-aloud needs a Malayalam voice installed.

- "Submit application" is a **demo submission**: it locks the application and creates a reference
  (`JSA-APP-…`) and final PDF, but nothing is sent to any government department. The PDF uses English
  labels; values typed in Malayalam are not drawn in the PDF (built-in PDF fonts) and are marked as such.

**Panchayat Dashboard limitations**
- **Citizen identity is a demo mechanism.** A random browser id is linked to a household with a
  household reference + link code from the survey slip. Real use needs proper citizen sign-in
  (e.g. an approved government identity provider). Not production authentication.
- Official sign-in uses hashed passwords, signed session cookies, CSRF tokens and a simple in-memory
  login rate limit. Production would add MFA, password reset, account lockout policies, a shared rate
  limiter, and HTTPS-only cookies.
- No real government system is connected: no DigiLocker, no department APIs, no SMS / email.
  Notifications appear inside the app only.
- The document-category → authority mapping is demo configuration; confirm with the actual departments.
- AI pre-check = Tesseract OCR + fixed rules (no LLM). PDFs are not OCR-read. Findings are advice only.
- SQLite + local private folder: no encryption at rest, backups, retention policy or local-server /
  cloud synchronisation yet. The code is organised (API layer, jurisdiction-scoped queries, private
  storage module) so these can be added without a rewrite.
- OCR-assisted entry of paper survey forms is designed for (surveys store original and current values)
  but not implemented; surveys are typed in manually.
- Licences are set by the platform operator through `manage_demo.py`; there is no billing.
- All demo panchayats, people, households and documents are fictional.
