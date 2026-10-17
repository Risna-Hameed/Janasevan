"""Original student-side tables (unchanged; now applied as migration 1)."""

STUDENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS citizen_profiles (
    client_id   TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,              -- JSON: only the fields the citizen chose to give
    preferred_language TEXT DEFAULT 'en',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_schemes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   TEXT NOT NULL,
    scheme_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (client_id, scheme_id)
);
CREATE TABLE IF NOT EXISTS application_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       TEXT NOT NULL,
    scheme_id       TEXT NOT NULL,
    form_data       TEXT NOT NULL,          -- JSON
    provided_items  TEXT NOT NULL,          -- JSON list of document ids
    missing_items   TEXT NOT NULL,          -- JSON list
    readiness_score INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('draft', 'demo_confirmed')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     TEXT NOT NULL,
    scheme_id     TEXT NOT NULL,
    reminder_date TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
);
"""
