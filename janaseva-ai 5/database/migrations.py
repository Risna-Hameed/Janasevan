"""Versioned, additive schema migrations.

Each migration runs once, inside a transaction, and is recorded in
schema_migrations. Migrations only CREATE - they never drop tables or delete
existing citizen data. To change the schema later, append a new migration.
"""
from database.db_schema_student import STUDENT_SCHEMA

PANCHAYAT_SCHEMA = """
-- Administrative hierarchy: a block panchayat contains several grama panchayats.
CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    district    TEXT NOT NULL,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS panchayats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER NOT NULL REFERENCES blocks(id),
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'grama' CHECK (kind IN ('grama')),
    is_demo     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    panchayat_id  INTEGER NOT NULL REFERENCES panchayats(id),
    number        INTEGER NOT NULL,
    name          TEXT NOT NULL,
    UNIQUE (panchayat_id, number)
);
CREATE TABLE IF NOT EXISTS panchayat_licences (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    panchayat_id     INTEGER NOT NULL UNIQUE REFERENCES panchayats(id),
    status           TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'expired')),
    expires_on       TEXT,
    features         TEXT NOT NULL,          -- JSON list, e.g. ["verification","surveys","analytics"]
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS official_users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    full_name      TEXT NOT NULL,
    password_hash  TEXT NOT NULL,            -- werkzeug scrypt hash, never plaintext
    role           TEXT NOT NULL CHECK (role IN ('panchayat_admin', 'verification_officer', 'data_entry', 'read_only')),
    panchayat_id   INTEGER NOT NULL REFERENCES panchayats(id),
    is_active      INTEGER NOT NULL DEFAULT 1,
    is_demo        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);
-- ward_id NULL = all wards of the official's panchayat
CREATE TABLE IF NOT EXISTS official_assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    official_id   INTEGER NOT NULL REFERENCES official_users(id),
    panchayat_id  INTEGER NOT NULL REFERENCES panchayats(id),
    ward_id       INTEGER REFERENCES wards(id),
    UNIQUE (official_id, panchayat_id, ward_id)
);

-- Door-to-door survey data
CREATE TABLE IF NOT EXISTS households (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    household_ref      TEXT NOT NULL UNIQUE,
    panchayat_id       INTEGER NOT NULL REFERENCES panchayats(id),
    ward_id            INTEGER NOT NULL REFERENCES wards(id),
    survey_ref         TEXT,
    survey_date        TEXT,
    address            TEXT NOT NULL,
    member_count       INTEGER NOT NULL DEFAULT 0,
    annual_income      INTEGER,               -- sensitive: shown only to permitted roles
    housing_condition  TEXT,
    drinking_water     TEXT,
    sanitation         TEXT,
    extra              TEXT NOT NULL DEFAULT '{}',
    validation_status  TEXT NOT NULL DEFAULT 'validated' CHECK (validation_status IN ('draft', 'awaiting_validation', 'validated', 'needs_correction')),
    citizen_link_code_hash TEXT,              -- demo linking code given on the survey slip (hashed)
    is_demo            INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS household_members (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    member_ref         TEXT NOT NULL UNIQUE,
    household_id       INTEGER NOT NULL REFERENCES households(id),
    panchayat_id       INTEGER NOT NULL REFERENCES panchayats(id),
    name               TEXT NOT NULL,
    age                INTEGER,
    education_status   TEXT,
    employment_status  TEXT,
    assistance_needs   TEXT,                  -- sensitive
    is_student         INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS survey_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_ref       TEXT NOT NULL UNIQUE,
    panchayat_id     INTEGER NOT NULL REFERENCES panchayats(id),
    ward_id          INTEGER NOT NULL REFERENCES wards(id),
    household_id     INTEGER REFERENCES households(id),
    status           TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'validated', 'needs_correction')),
    source           TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'ocr_proposed')),
    original_data    TEXT NOT NULL,          -- JSON as first entered, never overwritten
    current_data     TEXT NOT NULL,          -- JSON after corrections
    duplicate_of     INTEGER REFERENCES survey_records(id),
    reviewer_remarks TEXT,
    created_by       INTEGER REFERENCES official_users(id),
    validated_by     INTEGER REFERENCES official_users(id),
    is_demo          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS survey_responses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id   INTEGER NOT NULL REFERENCES survey_records(id),
    question    TEXT NOT NULL,
    answer      TEXT,
    UNIQUE (survey_id, question)
);
CREATE TABLE IF NOT EXISTS survey_corrections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id   INTEGER NOT NULL REFERENCES survey_records(id),
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    official_id INTEGER REFERENCES official_users(id),
    created_at  TEXT NOT NULL
);

-- Citizen <-> household link (demo identity, see README limitations)
CREATE TABLE IF NOT EXISTS citizen_links (
    client_id     TEXT PRIMARY KEY,
    household_id  INTEGER NOT NULL REFERENCES households(id),
    member_id     INTEGER REFERENCES household_members(id),
    panchayat_id  INTEGER NOT NULL REFERENCES panchayats(id),
    linked_at     TEXT NOT NULL
);

-- Documents are stored in a private folder, never under /static
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    panchayat_id   INTEGER NOT NULL REFERENCES panchayats(id),
    client_id      TEXT NOT NULL,
    storage_name   TEXT NOT NULL UNIQUE,      -- random name, no user input
    original_name  TEXT NOT NULL,
    mime_type      TEXT NOT NULL CHECK (mime_type IN ('application/pdf', 'image/png', 'image/jpeg')),
    size_bytes     INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    doc_reference  TEXT,
    issuing_authority TEXT,
    valid_until    TEXT,
    uploaded_at    TEXT NOT NULL,
    is_demo        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS verification_requests (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    request_ref          TEXT NOT NULL UNIQUE,
    client_id            TEXT NOT NULL,
    household_id         INTEGER NOT NULL REFERENCES households(id),
    member_id            INTEGER REFERENCES household_members(id),
    panchayat_id         INTEGER NOT NULL REFERENCES panchayats(id),
    ward_id              INTEGER NOT NULL REFERENCES wards(id),
    document_category    TEXT NOT NULL,
    purpose              TEXT NOT NULL,
    scheme_id            TEXT,
    status               TEXT NOT NULL CHECK (status IN ('submitted', 'queued_for_review', 'under_review',
                           'info_required', 'verified', 'unsuccessful', 'referred', 'cancelled')),
    assigned_official_id INTEGER REFERENCES official_users(id),
    citizen_remarks      TEXT,
    official_remarks     TEXT,               -- the remark the citizen may see
    resubmission_count   INTEGER NOT NULL DEFAULT 0,
    is_demo              INTEGER NOT NULL DEFAULT 0,
    submitted_at         TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    closed_at            TEXT
);
CREATE TABLE IF NOT EXISTS verification_request_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   INTEGER NOT NULL REFERENCES verification_requests(id),
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    round        INTEGER NOT NULL DEFAULT 1,  -- 1 = first submission, 2+ = resubmissions
    added_at     TEXT NOT NULL,
    UNIQUE (request_id, document_id)
);
CREATE TABLE IF NOT EXISTS verification_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES verification_requests(id),
    official_id     INTEGER NOT NULL REFERENCES official_users(id),
    kind            TEXT NOT NULL CHECK (kind IN ('draft', 'final')),
    outcome         TEXT CHECK (outcome IN ('verified', 'unsuccessful', 'info_required', 'referred')),
    checklist       TEXT NOT NULL DEFAULT '{}',
    remarks         TEXT,
    internal_notes  TEXT,
    missing_items   TEXT NOT NULL DEFAULT '[]',
    referral_authority TEXT,
    referral_category  TEXT,
    official_reference TEXT,
    receipt_ref     TEXT UNIQUE,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   INTEGER NOT NULL REFERENCES verification_requests(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor_type   TEXT NOT NULL CHECK (actor_type IN ('citizen', 'official', 'system')),
    actor_id     TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_type  TEXT NOT NULL CHECK (recipient_type IN ('citizen', 'official')),
    client_id       TEXT,
    panchayat_id    INTEGER REFERENCES panchayats(id),
    official_id     INTEGER REFERENCES official_users(id),
    request_id      INTEGER REFERENCES verification_requests(id),
    title_en        TEXT NOT NULL,
    title_ml        TEXT NOT NULL,
    body_en         TEXT NOT NULL,
    body_ml         TEXT NOT NULL,
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    panchayat_id  INTEGER,
    actor_type    TEXT NOT NULL,
    actor_id      TEXT,
    action        TEXT NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    detail        TEXT,                       -- never passwords, tokens or document contents
    ip            TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_households_scope ON households (panchayat_id, ward_id);
CREATE INDEX IF NOT EXISTS ix_members_household ON household_members (household_id);
CREATE INDEX IF NOT EXISTS ix_requests_scope ON verification_requests (panchayat_id, ward_id, status);
CREATE INDEX IF NOT EXISTS ix_requests_client ON verification_requests (client_id);
CREATE INDEX IF NOT EXISTS ix_history_request ON verification_history (request_id);
CREATE INDEX IF NOT EXISTS ix_notifications_client ON notifications (client_id, is_read);
CREATE INDEX IF NOT EXISTS ix_notifications_official ON notifications (panchayat_id, recipient_type);
CREATE INDEX IF NOT EXISTS ix_audit_scope ON audit_logs (panchayat_id, created_at);
CREATE INDEX IF NOT EXISTS ix_surveys_scope ON survey_records (panchayat_id, ward_id, status);
"""

MIGRATIONS = [
    (1, "student tables (profiles, saved schemes, drafts, reminders)", STUDENT_SCHEMA),
    (2, "panchayat dashboard: jurisdiction, officials, households, surveys, verification workflow", PANCHAYAT_SCHEMA),
]


def apply_migrations(conn, now):
    """Apply every migration not yet recorded. Each runs in its own transaction."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL)""")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, description, sql in MIGRATIONS:
        if version in done:
            continue
        try:
            conn.execute("BEGIN")
            for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                         (version, description, now()))
            conn.execute("COMMIT")
            applied.append(version)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return applied
