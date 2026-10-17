"""Daily scheme update endpoints.

Public (student portal):   GET  /api/schemes/updates        what was added or changed, when the next check is
Admin (panchayat admin):   GET  /api/panchayat/scheme-updates
                           POST /api/panchayat/scheme-updates/run              check now (runs in the background)
                           POST /api/panchayat/scheme-updates/<id>/hide        {"hidden": true|false}
                           POST /api/panchayat/scheme-updates/<id>/rechecked   official re-read a changed document
"""
import threading
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request

from database.db import get_db
from services import retrieval, scheme_updater
from services.panchayat_auth import audit, require_official

bp = Blueprint("scheme_updates", __name__)
admin_bp = Blueprint("scheme_updates_admin", __name__)
NEW_FOR_DAYS = 30


@bp.get("/schemes/updates")
def public_updates():
    st = scheme_updater.status(current_app.config.get("SCHEME_UPDATE_HOUR_IST", 6))
    since = (date.today() - timedelta(days=NEW_FOR_DAYS)).isoformat()
    visible = {s["id"]: s for s in retrieval.load_schemes()}
    new = [a for a in st["auto_added"] if a["id"] in visible and a["added_on"] >= since]
    new.sort(key=lambda a: a["added_on"], reverse=True)
    changed = [{"id": k, "name_en": visible[k].get("name_en"), "name_ml": visible[k].get("name_ml"),
                "changed_on": v["changed_on"]}
               for k, v in st["doc_changes"].items() if k in visible and not v.get("dismissed")]
    return jsonify({"last_run": st["last_run"], "next_run": st["next_run"], "running": st["running"],
                    "auto_update_on": bool(current_app.config.get("SCHEME_AUTO_UPDATE")),
                    "new_schemes": [{"id": a["id"], "name_en": a["name_en"], "added_on": a["added_on"],
                                     "deadline": a["deadline"], "is_demo": a["is_demo"]} for a in new],
                    "changed_schemes": changed,
                    "catalog_size": len(visible)})


@admin_bp.get("/scheme-updates")
@require_official("manage_scheme_updates")
def admin_status():
    return jsonify(scheme_updater.status(current_app.config.get("SCHEME_UPDATE_HOUR_IST", 6)))


@admin_bp.post("/scheme-updates/run")
@require_official("manage_scheme_updates")
def admin_run():
    if scheme_updater.status()["running"]:
        return jsonify({"started": False, "message": "An update is already running."}), 409
    audit("scheme_update_started", "catalog", "all")
    get_db().commit()
    threading.Thread(target=scheme_updater.run_update, name="scheme-update-now", daemon=True).start()
    return jsonify({"started": True, "message": "Checking official pages now. This can take a minute."}), 202


@admin_bp.post("/scheme-updates/<scheme_id>/hide")
@require_official("manage_scheme_updates")
def admin_hide(scheme_id):
    hidden = bool((request.get_json(silent=True) or {}).get("hidden", True))
    if not scheme_updater.set_hidden(scheme_id, hidden):
        return jsonify({"error": "Not an automatically added scheme."}), 404
    audit("auto_scheme_hidden" if hidden else "auto_scheme_shown", "scheme", scheme_id)
    get_db().commit()
    return jsonify({"ok": True, "hidden": hidden})


@admin_bp.post("/scheme-updates/<scheme_id>/rechecked")
@require_official("manage_scheme_updates")
def admin_rechecked(scheme_id):
    if not scheme_updater.clear_doc_change(scheme_id):
        return jsonify({"error": "No open document change for this scheme."}), 404
    audit("scheme_document_rechecked", "scheme", scheme_id)
    get_db().commit()
    return jsonify({"ok": True})
