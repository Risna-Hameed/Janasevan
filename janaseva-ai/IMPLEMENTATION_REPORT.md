# Implementation report: Panchayat Administration Dashboard

Added to the existing JANASEVA AI project (Flask + built-in sqlite3 + Bootstrap 5 + plain JavaScript).
No new framework, build step or Python package was introduced. The student portal at `/` keeps
working, and all 22 original tests still pass.

## Files created (15)

| File | Purpose |
|---|---|
| `database/migrations.py` | Versioned, additive migrations (`schema_migrations` table). Migration 1 = existing student tables, migration 2 = Panchayat module |
| `database/db_schema_student.py` | The original student schema, moved out of `db.py` unchanged |
| `database/seed_panchayat.py` | Fictional demo data + safe demo-only reset |
| `manage_demo.py` | CLI: `seed`, `reset`, `licence CODE STATUS`, `accounts` |
| `services/panchayat_auth.py` | Official sign-in, password hashing, sessions, roles and permissions, jurisdiction scope SQL, licence checks, CSRF, login rate limit, audit logging |
| `services/verification_workflow.py` | Document categories (verify vs refer-only), status state machine, citizen notifications, citizen-safe view |
| `services/private_files.py` | Private document storage; file type checked from bytes; random storage names |
| `services/verification_receipt.py` | Verification record PDF, worded as "not a certificate, not an approval" |
| `routes/panchayat_routes.py` | `/panchayat` page; sign-in; dashboard; request list, detail, actions; AI pre-check; protected documents; history; notifications |
| `routes/panchayat_records_routes.py` | Households, surveys, analytics, administration |
| `routes/citizen_verification_routes.py` | Citizen link, submit, resubmit, cancel, status, receipt, notifications |
| `templates/panchayat.html` | Dashboard shell: sign-in, sidebar with 8 sections, review workspace, dialogs |
| `static/css/panchayat.css` | Dark-green administrative theme, responsive sidebar |
| `static/js/panchayat.js` | All dashboard behaviour (no framework) |
| `tests/test_panchayat.py` | 20 tests on a temporary database |

## Files modified (10)

| File | Change |
|---|---|
| `app.py` | Registers 4 new route groups; seeds demo data on first start; security headers; proper 404 for missing static files and the `/panchayat` fallback |
| `config.py` | Random persistent `SECRET_KEY` when not set; private upload folder; secure session-cookie settings; processing target; `SEED_DEMO` |
| `database/db.py` | Runs migrations; enables foreign keys; `transaction()` helper for all-or-nothing updates |
| `templates/index.html` | New "Document Verification" page and nav item with an unread badge |
| `static/js/app.js` | Household link, request submission with upload, status list, resubmit, cancel, record download, notifications with 30-second polling |
| `static/js/api.js` | 11 citizen verification API calls |
| `static/i18n/en.json`, `ml.json` | 41 new labels in English and Malayalam (all 269 keys present in both) |
| `tests/test_p3.py` | Now uses a temporary database instead of the real one |
| `README.md` | Dashboard demo, accounts, environment variables, API, workflow, limitations |

## Database changes (migration 2, 18 tables)

`blocks` → `panchayats` (grama, linked to block) → `wards`; `panchayat_licences`;
`official_users` (hashed passwords, role) + `official_assignments` (panchayat / ward scope);
`households`, `household_members`, `survey_records` (original vs current data), `survey_responses`,
`survey_corrections`; `citizen_links`; `documents` (private storage metadata);
`verification_requests`, `verification_request_documents` (submission rounds), `verification_reviews`
(draft / final), `verification_history`; `notifications`; `audit_logs`. Includes foreign keys, CHECK
constraints on every status column, unique references and 9 indexes. Existing tables and data are
untouched; tested by upgrading a pre-feature database.

## Security measures implemented

- Passwords stored as werkzeug scrypt hashes only; never written to the audit log (tested).
- Signed, HttpOnly, SameSite=Lax session cookie, 8-hour lifetime; a `Secure` option for HTTPS.
- CSRF token required on every state-changing official request.
- Jurisdiction enforced inside the SQL of every query. Requests, households, documents and surveys
  outside the official's panchayat or wards return 404. Changing IDs, URLs, query strings or bodies
  doesn't widen access, and cross-scope attempts are audited.
- Role permissions checked on the server for every endpoint; hidden buttons are cosmetic only.
- Licence must be active for sign-in and for every call; a licence never replaces sign-in.
- Uploads: 5 MB limit, PDF / PNG / JPEG detected from the file bytes, random storage names, private
  folder outside `/static`, `no-store` and `nosniff` headers; downloads only for permitted roles,
  and every view or download is audited.
- The workflow state machine rejects any transition not explicitly allowed (409).
- Verify needs the full checklist plus explicit confirmation. "Unsuccessful" needs a reason category
  plus a written explanation of at least 15 characters. Refer-only categories cannot be verified.
- Notifications contain only the reference and status; no personal, financial or document details.
- Rate limits on official sign-in and on citizen household linking (in-memory).

## Tests executed

| Suite | Result |
|---|---|
| `tests/test_p3.py` (existing) | 11 / 11 pass |
| `tests/test_engine_ranker.py` (existing) | 11 / 11 pass |
| `tests/test_panchayat.py` (new) | 20 / 20 pass |
| Live acceptance demo (17 steps, real server over HTTP) | 17 / 17 pass |

The new tests cover dashboard loading and real-database statistics, listing, filters and pagination,
end-to-end verification with notification and receipt, correction plus resubmission (history kept),
referral and refer-only categories, invalid transitions, missing fields, other-officer and ward
scope, household summary vs sensitive access, survey create / correct / submit / validate with
duplicate detection, aggregate-only analytics, tenant isolation (households, requests, documents,
tampered queries), unauthorised access (anonymous, citizen, read-only, data entry, missing CSRF,
suspended licence), protected documents, upload validation and server-side jurisdiction, AI findings
not changing status, user management and password hashing, existing student features, and migration
of an old database.

**Not tested here:** the dashboard and student pages were not opened in a real browser, because none
was available in the build environment. Every API the pages call was exercised and all scripts pass
a syntax check, but layout and click-through should be checked once on a laptop.

## Known limitations / needs real government integration

- **Citizen identity** is a demonstration mechanism (browser id + household link code). It needs a
  proper citizen identity provider before any real use.
- **Official accounts** need MFA, password reset and central account management for production.
- **No external integrations:** DigiLocker, department APIs, SMS and email are not connected, and no
  real panchayat data is used.
- **Authority mapping** for document categories is demo configuration; it must be confirmed with the
  revenue and other departments.
- **Infrastructure:** encryption at rest, backups, retention / deletion policy, local-server-to-cloud
  synchronisation and a shared rate limiter are designed for but not built.
- **OCR for paper survey forms** is not implemented (the data model supports proposed vs validated values).
- **PDF documents** are previewed but not OCR-read by the AI pre-check.

## Daily scheme updates (added)

- `services/scheme_updater.py`: a background thread, started by `create_app`, runs once a day (06:00 IST, or straight away if a day was missed). It reads the allow-listed official listing pages (`data/scheme_sources.json`), publishes new schemes immediately with the label "New · added automatically", reads the facts written in each notice (HTML or PDF via pypdf) and keeps the source sentence as evidence. It re-hashes the official documents of the curated schemes to flag changes, and removes auto-added schemes no longer listed for 3 runs. robots.txt, a 2 s gap between requests, a size cap, a timeout, and one run at a time (thread lock plus a lock file shared across processes).
- Storage: `instance/scheme_updates/` (auto_schemes.json, updater_state.json, admin_actions.json). There is no DB migration. `retrieval.load_schemes()` merges the auto-added records, so search, the chatbot, eligibility and BestFit include them automatically.
- Honesty: auto-read rules have `verified: false`, so the engine never returns `potentially_eligible` for them. `application_submission.check` sends the student to the official notice. `benefit_value_inr` stays null, so ranking never uses an amount that wasn't verified.
- UI: an updates strip on Discover, a "New" badge, an "Official document changed" notice, and an evidence list in the details view. Panchayat admin has a **Scheme Updates** section (Check now, Hide/Show, Mark re-read), and every action goes into the audit log.
- CLI: `manage_schemes.py update [source] | status | hide | show`.
- Tests: `tests/test_scheme_updater.py` (9). It needs no internet: it uses the fictional demo feed and a fake fetcher.
