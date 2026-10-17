"""SQLite storage. Uses Python's built-in sqlite3 - nothing to install.

Scheme data is NOT copied into the database: data/schemes.json stays the single
source of truth. The schema is created by versioned migrations
(database/migrations.py) that never drop tables or delete citizen data.

Student-side rows are keyed by client_id, a random id the browser creates.
Panchayat-side rows are keyed by panchayat_id (and ward_id) so every query can be
limited to the official's jurisdiction.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import current_app, g

from database.db_schema_student import STUDENT_SCHEMA as SCHEMA  # noqa: F401  (kept for compatibility)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None          # autocommit; multi-step changes use transaction()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db=None):
    """All-or-nothing block: either every statement inside is saved, or none is."""
    db = db or get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def init_db(app):
    from database.migrations import apply_migrations
    with app.app_context():
        apply_migrations(get_db(), now)
    app.teardown_appcontext(close_db)


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("profile", "form_data", "provided_items", "missing_items", "checklist", "features",
              "original_data", "current_data", "extra", "change_notes"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d
