"""Document verification workflow: categories, the status state machine and notifications.

Important distinction kept throughout:
  * The panchayat RECORDS AN ADMINISTRATIVE VERIFICATION FINDING (e.g. "the details on this
    document match our household survey record"). It does not issue certificates.
  * Some documents must be issued or verified by another competent authority; for those
    the panchayat can only refer, ask for corrections, or record that verification was
    not possible.
  * Verification never sanctions a scholarship or releases any benefit.
The authority mapping below is DEMO CONFIGURATION and must be confirmed with the
relevant departments before real use.
"""
import json
from datetime import date

from database.db import now

STATUSES = {
    "submitted": ("Submitted", "സമർപ്പിച്ചു"),
    "queued_for_review": ("Queued for review", "പരിശോധനയ്ക്കായി കാത്തിരിക്കുന്നു"),
    "under_review": ("Under review", "പരിശോധനയിൽ"),
    "info_required": ("Additional information required", "കൂടുതൽ വിവരങ്ങൾ ആവശ്യമാണ്"),
    "verified": ("Verification completed", "പരിശോധന പൂർത്തിയായി"),
    "unsuccessful": ("Verification unsuccessful", "പരിശോധന വിജയിച്ചില്ല"),
    "referred": ("Referred to another authority", "മറ്റൊരു അധികാരിയിലേക്ക് റഫർ ചെയ്തു"),
    "cancelled": ("Cancelled", "റദ്ദാക്കി"),
}
OPEN_STATUSES = ("submitted", "queued_for_review", "under_review", "info_required")
CLOSED_STATUSES = ("verified", "unsuccessful", "referred", "cancelled")

# (from, to) -> who may make that move. Anything not listed is rejected.
TRANSITIONS = {
    ("submitted", "queued_for_review"): {"system"},
    ("queued_for_review", "under_review"): {"official"},
    ("under_review", "info_required"): {"official"},
    ("under_review", "verified"): {"official"},
    ("under_review", "unsuccessful"): {"official"},
    ("under_review", "referred"): {"official"},
    ("info_required", "queued_for_review"): {"citizen"},       # citizen resubmits
    ("submitted", "cancelled"): {"citizen"},
    ("queued_for_review", "cancelled"): {"citizen"},
    ("info_required", "cancelled"): {"citizen"},
}

COMMON_CHECKS = {
    "legible": ("Document is clear and readable", "രേഖ വ്യക്തവും വായിക്കാവുന്നതുമാണ്"),
    "name_matches_record": ("Name matches the household survey record", "പേര് കുടുംബ സർവേ രേഖയുമായി യോജിക്കുന്നു"),
    "ward_matches_record": ("Ward / address matches the household record", "വാർഡ് / വിലാസം കുടുംബ രേഖയുമായി യോജിക്കുന്നു"),
    "no_visible_alteration": ("No visible alteration or tampering (human check)", "ദൃശ്യമായ തിരുത്തലുകൾ ഇല്ല (മനുഷ്യ പരിശോധന)"),
}

# scope: "record_check" = panchayat may record that details match its records
#        "refer_only"   = must be referred; the Verify action is blocked
DOCUMENT_CATEGORIES = {
    "residence_proof": {
        "name_en": "Residence proof", "name_ml": "താമസ തെളിവ്", "scope": "record_check",
        "checks": ["legible", "name_matches_record", "ward_matches_record", "no_visible_alteration"],
        "note_en": "Panchayat checks the stated residence against its household survey record.",
    },
    "income_certificate": {
        "name_en": "Income certificate", "name_ml": "വരുമാന സർട്ടിഫിക്കറ്റ്", "scope": "record_check",
        "checks": ["legible", "name_matches_record", "ward_matches_record", "no_visible_alteration", "income_consistent"],
        "extra_checks": {"income_consistent": ("Stated income is consistent with the survey record, or the difference is explained",
                                               "വരുമാനം സർവേ രേഖയുമായി യോജിക്കുന്നു / വ്യത്യാസം വിശദീകരിച്ചു")},
        "note_en": "The certificate itself is issued by the revenue authority. The panchayat only records whether its details match the household record.",
    },
    "educational_certificate": {
        "name_en": "Educational certificate", "name_ml": "വിദ്യാഭ്യാസ സർട്ടിഫിക്കറ്റ്", "scope": "record_check",
        "checks": ["legible", "name_matches_record", "no_visible_alteration"],
        "note_en": "Marks and results can only be confirmed by the issuing board or university. The panchayat records an identity/record match only.",
    },
    "community_certificate": {
        "name_en": "Community / category certificate", "name_ml": "സമുദായ / വിഭാഗ സർട്ടിഫിക്കറ്റ്", "scope": "refer_only",
        "checks": ["legible"],
        "refer_to": "Revenue authority that issues community certificates (demo mapping - confirm locally)",
        "note_en": "Community status is certified by the competent revenue authority, not the panchayat. Refer the request.",
    },
    "non_creamy_layer_certificate": {
        "name_en": "Non-creamy-layer certificate", "name_ml": "നോൺ-ക്രീമിലെയർ സർട്ടിഫിക്കറ്റ്", "scope": "refer_only",
        "checks": ["legible"],
        "refer_to": "Revenue authority that issues non-creamy-layer certificates (demo mapping - confirm locally)",
        "note_en": "Must be issued and verified by the competent revenue authority. Refer the request.",
    },
    "other_supporting": {
        "name_en": "Other supporting document", "name_ml": "മറ്റ് അനുബന്ധ രേഖ", "scope": "record_check",
        "checks": ["legible", "name_matches_record", "no_visible_alteration"],
        "note_en": "Panchayat records an identity/record match only.",
    },
}

CORRECTION_REASONS = {
    "unreadable": "Document image is unreadable",
    "missing_page": "Required supporting page is missing",
    "clarification": "Information needs clarification",
    "updated_document": "An updated document is required",
    "additional_evidence": "Additional evidence is needed under the applicable procedure",
}
UNSUCCESSFUL_REASONS = {
    "record_mismatch": "Details do not match the panchayat household record",
    "not_in_jurisdiction": "Applicant is not recorded in this panchayat's jurisdiction",
    "document_invalid": "Document is incomplete, expired or not the type requested",
    "suspected_alteration": "Document appears altered; original must be produced in person",
}
REFERRAL_CATEGORIES = {
    "revenue": "Revenue authority (Village / Taluk office)",
    "education": "Issuing board or university",
    "civil_registration": "Civil registration authority",
    "other": "Other competent authority",
}


class WorkflowError(ValueError):
    """Invalid transition or missing information - returned to the caller as HTTP 409/400."""


def category_info(code):
    return DOCUMENT_CATEGORIES.get(code)


def checklist_for(code):
    cat = DOCUMENT_CATEGORIES[code]
    labels = {**COMMON_CHECKS, **cat.get("extra_checks", {})}
    return [{"id": c, "label_en": labels[c][0], "label_ml": labels[c][1]} for c in cat["checks"]]


def status_label(status, lang="en"):
    en, ml = STATUSES[status]
    return ml if lang == "ml" else en


def transition(db, req, to_status, actor_type, actor_id, note=None):
    """Move a request along the workflow. Caller must run this inside a transaction."""
    frm = req["status"]
    allowed = TRANSITIONS.get((frm, to_status))
    if not allowed:
        raise WorkflowError(f"Cannot move a request from '{STATUSES[frm][0]}' to '{STATUSES.get(to_status, (to_status,))[0]}'.")
    if actor_type not in allowed:
        raise WorkflowError("You are not allowed to make this change.")
    closed = now() if to_status in CLOSED_STATUSES else None
    db.execute("UPDATE verification_requests SET status=?, updated_at=?, closed_at=COALESCE(?, closed_at) WHERE id=?",
               (to_status, now(), closed, req["id"]))
    db.execute("INSERT INTO verification_history (request_id, from_status, to_status, actor_type, actor_id, note, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)", (req["id"], frm, to_status, actor_type, str(actor_id or ""), note, now()))
    return to_status


NEXT_STEPS = {
    "submitted": ("Your request has been sent to your panchayat.", "നിങ്ങളുടെ അപേക്ഷ പഞ്ചായത്തിലേക്ക് അയച്ചു."),
    "queued_for_review": ("Your panchayat has received the request and will review it.", "പഞ്ചായത്ത് അപേക്ഷ സ്വീകരിച്ചു; പരിശോധിക്കും."),
    "under_review": ("An official is reviewing your document.", "ഒരു ഉദ്യോഗസ്ഥൻ നിങ്ങളുടെ രേഖ പരിശോധിക്കുന്നു."),
    "info_required": ("Read the official's remarks and upload the requested document.", "ഉദ്യോഗസ്ഥന്റെ കുറിപ്പ് വായിച്ച് ആവശ്യപ്പെട്ട രേഖ അപ്‌ലോഡ് ചെയ്യുക."),
    "verified": ("Download your verification record and use it when you apply through the official scholarship process. This is not a scholarship approval.",
                 "പരിശോധനാ രേഖ ഡൗൺലോഡ് ചെയ്ത് ഔദ്യോഗിക സ്കോളർഷിപ്പ് അപേക്ഷയിൽ ഉപയോഗിക്കുക. ഇത് സ്കോളർഷിപ്പ് അംഗീകാരമല്ല."),
    "unsuccessful": ("Read the reason given. You may submit a new request with the correct document.",
                     "കാരണം വായിക്കുക. ശരിയായ രേഖയുമായി പുതിയ അപേക്ഷ നൽകാം."),
    "referred": ("Contact the authority named in the remarks to complete this verification.",
                 "പരിശോധന പൂർത്തിയാക്കാൻ കുറിപ്പിൽ പറയുന്ന അധികാരിയെ ബന്ധപ്പെടുക."),
    "cancelled": ("This request was cancelled.", "ഈ അപേക്ഷ റദ്ദാക്കി."),
}


def notify_citizen(db, req, status):
    """Notification text contains only the reference and status - never document or personal details."""
    en, ml = STATUSES[status]
    nen, nml = NEXT_STEPS[status]
    db.execute("INSERT INTO notifications (recipient_type, client_id, panchayat_id, request_id, title_en, title_ml, body_en, body_ml, created_at) "
               "VALUES ('citizen', ?, ?, ?, ?, ?, ?, ?, ?)",
               (req["client_id"], req["panchayat_id"], req["id"], f"{req['request_ref']}: {en}", f"{req['request_ref']}: {ml}", nen, nml, now()))


def notify_officials(db, req, text_en, text_ml):
    db.execute("INSERT INTO notifications (recipient_type, panchayat_id, request_id, title_en, title_ml, body_en, body_ml, created_at) "
               "VALUES ('official', ?, ?, ?, ?, ?, ?, ?)",
               (req["panchayat_id"], req["id"], req["request_ref"], req["request_ref"], text_en, text_ml, now()))


def make_ref(prefix, row_id, year=None):
    return f"JSA-{prefix}-{year or date.today().year}-{row_id:05d}"


def get_request(db, request_id):
    row = db.execute("SELECT * FROM verification_requests WHERE id=?", (request_id,)).fetchone()
    return dict(row) if row else None


def citizen_view(db, req, lang="en"):
    """What the citizen may see: no internal notes, no official names or IDs."""
    history = [dict(h) for h in db.execute(
        "SELECT to_status, actor_type, created_at FROM verification_history WHERE request_id=? ORDER BY id", (req["id"],))]
    final = db.execute("SELECT receipt_ref, outcome, referral_authority FROM verification_reviews "
                       "WHERE request_id=? AND kind='final' ORDER BY id DESC LIMIT 1", (req["id"],)).fetchone()
    cat = DOCUMENT_CATEGORIES.get(req["document_category"], {})
    en, ml = NEXT_STEPS[req["status"]]
    return {
        "id": req["id"], "request_ref": req["request_ref"], "status": req["status"],
        "status_en": STATUSES[req["status"]][0], "status_ml": STATUSES[req["status"]][1],
        "document_category": req["document_category"], "category_en": cat.get("name_en"), "category_ml": cat.get("name_ml"),
        "purpose": req["purpose"], "scheme_id": req["scheme_id"],
        "submitted_at": req["submitted_at"], "updated_at": req["updated_at"],
        "official_remarks": req["official_remarks"], "resubmission_count": req["resubmission_count"],
        "can_resubmit": req["status"] == "info_required",
        "can_cancel": req["status"] in ("submitted", "queued_for_review", "info_required"),
        "receipt_available": bool(final and final["outcome"] == "verified" and final["receipt_ref"]),
        "receipt_ref": final["receipt_ref"] if final and final["outcome"] == "verified" else None,
        "referral_authority": final["referral_authority"] if final and final["outcome"] == "referred" else None,
        "next_step_en": en, "next_step_ml": ml,
        "history": [{"status": h["to_status"], "status_en": STATUSES[h["to_status"]][0], "status_ml": STATUSES[h["to_status"]][1],
                     "by": h["actor_type"], "at": h["created_at"]} for h in history],
        "disclaimer_en": "Document verification is not approval of any scholarship or benefit. Apply through the official process.",
        "disclaimer_ml": "രേഖ പരിശോധന ഏതെങ്കിലും സ്കോളർഷിപ്പിന്റെയോ ആനുകൂല്യത്തിന്റെയോ അംഗീകാരമല്ല. ഔദ്യോഗിക രീതിയിൽ അപേക്ഷിക്കുക.",
    }


def dumps(v):
    return json.dumps(v, ensure_ascii=False)
