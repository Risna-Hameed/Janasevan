"""Official sign-in, roles, jurisdiction scope, licence checks, CSRF and audit logging.

Security model (prototype):
  * Officials sign in with username + password. Passwords are stored only as
    werkzeug scrypt hashes.
  * The session is a signed, HttpOnly, SameSite=Lax Flask cookie.
  * Every state-changing request must send the X-CSRF-Token header issued at sign-in.
  * Every query is limited to the official's panchayat (and assigned wards) on the
    server. Hiding buttons in the browser is NOT relied on.
  * The panchayat's licence must be active - but a licence never replaces sign-in.
"""
import json
import secrets
import time
from collections import defaultdict
from datetime import date
from functools import wraps

from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import get_db, now

ROLE_PERMISSIONS = {
    "panchayat_admin": {"view_dashboard", "view_requests", "review_requests", "view_documents", "download_documents",
                        "view_households", "view_sensitive", "edit_households", "manage_surveys", "validate_surveys",
                        "view_analytics", "manage_users", "view_audit"},
    "verification_officer": {"view_dashboard", "view_requests", "review_requests", "view_documents", "download_documents",
                             "view_households", "view_sensitive", "view_analytics"},
    "data_entry": {"view_dashboard", "view_households", "manage_surveys", "view_analytics"},
    "read_only": {"view_dashboard", "view_requests", "view_households", "view_analytics"},
}
ROLE_LABELS = {"panchayat_admin": "Panchayat Administrator", "verification_officer": "Verification Officer",
               "data_entry": "Survey Data Entry Operator", "read_only": "Read-Only Official"}

_login_attempts = defaultdict(list)   # (ip, username) -> timestamps; in-memory, per process
LOGIN_WINDOW_S, LOGIN_MAX = 300, 5


def hash_password(pw):
    try:
        return generate_password_hash(pw)
    except (AttributeError, ValueError):
        return generate_password_hash(pw, method="pbkdf2:sha256")


def audit(action, target_type=None, target_id=None, detail=None, panchayat_id=None, actor_type=None, actor_id=None, db=None):
    """Write one audit row. `detail` must never contain passwords, tokens or document contents."""
    off = getattr(g, "official", None)
    (db or get_db()).execute(
        "INSERT INTO audit_logs (panchayat_id, actor_type, actor_id, action, target_type, target_id, detail, ip, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (panchayat_id if panchayat_id is not None else (off["panchayat_id"] if off else None),
         actor_type or ("official" if off else "system"),
         str(actor_id if actor_id is not None else (off["id"] if off else "")),
         action, target_type, None if target_id is None else str(target_id),
         json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else detail,
         request.remote_addr if request else None, now()))


def licence_for(panchayat_id, db=None):
    row = (db or get_db()).execute("SELECT * FROM panchayat_licences WHERE panchayat_id=?", (panchayat_id,)).fetchone()
    if not row:
        return {"valid": False, "status": "missing", "features": []}
    features = json.loads(row["features"])
    expired = bool(row["expires_on"]) and row["expires_on"] < date.today().isoformat()
    status = "expired" if expired and row["status"] == "active" else row["status"]
    return {"valid": status == "active", "status": status, "expires_on": row["expires_on"], "features": features}


def rate_limited(username):
    key = (request.remote_addr, (username or "").lower())
    recent = [t for t in _login_attempts[key] if time.time() - t < LOGIN_WINDOW_S]
    _login_attempts[key] = recent
    return len(recent) >= LOGIN_MAX


def record_failed_login(username):
    _login_attempts[(request.remote_addr, (username or "").lower())].append(time.time())


def login(username, password):
    """Returns (official_row, error_message, http_status)."""
    if rate_limited(username):
        return None, "Too many sign-in attempts. Please wait 5 minutes.", 429
    db = get_db()
    row = db.execute("SELECT * FROM official_users WHERE username=?", ((username or "").strip().lower(),)).fetchone()
    # Same message for unknown user and wrong password, so usernames cannot be guessed
    if not row or not row["is_active"] or not check_password_hash(row["password_hash"], password or ""):
        record_failed_login(username)
        audit("login_failed", "official_user", username, panchayat_id=row["panchayat_id"] if row else None,
              actor_type="anonymous", actor_id="")
        return None, "Username or password is incorrect.", 401
    lic = licence_for(row["panchayat_id"])
    if not lic["valid"]:
        audit("login_blocked_licence", "panchayat", row["panchayat_id"], panchayat_id=row["panchayat_id"],
              actor_type="official", actor_id=row["id"])
        return None, f"This panchayat's licence is {lic['status']}. Contact the platform administrator.", 403
    session.clear()
    session.permanent = True
    session["official_id"] = row["id"]
    session["csrf"] = secrets.token_urlsafe(32)
    db.execute("UPDATE official_users SET last_login_at=? WHERE id=?", (now(), row["id"]))
    audit("login", "official_user", row["id"], panchayat_id=row["panchayat_id"], actor_type="official", actor_id=row["id"])
    return row, None, 200


def logout():
    off = current_official()
    if off:
        audit("logout", "official_user", off["id"])
    session.clear()


def current_official():
    if "official" in g:
        return g.official
    oid = session.get("official_id")
    if not oid:
        g.official = None
        return None
    db = get_db()
    row = db.execute("SELECT * FROM official_users WHERE id=? AND is_active=1", (oid,)).fetchone()
    if not row:
        session.clear()
        g.official = None
        return None
    off = dict(row)
    wards = [r["ward_id"] for r in db.execute(
        "SELECT ward_id FROM official_assignments WHERE official_id=? AND panchayat_id=?", (off["id"], off["panchayat_id"]))]
    off["ward_ids"] = None if (not wards or None in wards) else wards       # None = all wards
    off["permissions"] = sorted(ROLE_PERMISSIONS[off["role"]])
    off.pop("password_hash", None)
    g.official = off
    return off


def scope_sql(alias=""):
    """SQL condition + params that restrict rows to the signed-in official's jurisdiction."""
    off = g.official
    p = f"{alias}." if alias else ""
    sql, params = f"{p}panchayat_id = ?", [off["panchayat_id"]]
    if off["ward_ids"] is not None:
        sql += f" AND {p}ward_id IN ({','.join('?' * len(off['ward_ids']))})"
        params += off["ward_ids"]
    return sql, params


def in_scope(panchayat_id, ward_id):
    off = g.official
    if panchayat_id != off["panchayat_id"]:
        return False
    return off["ward_ids"] is None or ward_id in off["ward_ids"]


def require_official(permission=None, feature=None):
    """Decorator: signed-in official + permission + active licence (+ CSRF for writes)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            off = current_official()
            if not off:
                return jsonify({"error": "Please sign in."}), 401
            lic = licence_for(off["panchayat_id"])
            if not lic["valid"]:
                return jsonify({"error": f"Panchayat licence is {lic['status']}."}), 403
            if feature and feature not in lic["features"]:
                return jsonify({"error": "This feature is not enabled in your panchayat's licence."}), 403
            if permission and permission not in ROLE_PERMISSIONS[off["role"]]:
                audit("permission_denied", "endpoint", request.path, detail={"needed": permission})
                return jsonify({"error": "You do not have permission for this action."}), 403
            if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                sent = request.headers.get("X-CSRF-Token", "")
                if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                    return jsonify({"error": "Security token missing or invalid. Please reload the page."}), 403
            return fn(*a, **kw)
        return wrapper
    return deco
