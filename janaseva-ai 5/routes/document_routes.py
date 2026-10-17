"""Document OCR, consistency check and Akshaya packet routes (owned by P3)."""
import json

from flask import Blueprint, Response, jsonify, request

from services import document_service, packet_service

bp = Blueprint("documents", __name__)


@bp.post("/documents/analyze")
def analyze_document():
    """multipart/form-data: file=<image>, doc_type=income_cert, lang=en|ml,
    reference=<JSON string of profile/draft fields, e.g. {"name": "...", "annual_income": 120000}>"""
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded. Choose an image or enter details manually."}), 400
    lang = request.form.get("lang", "en")
    try:
        result = document_service.analyze(upload.filename, upload.read(),
                                          request.form.get("doc_type", "income_cert"), lang)
    except document_service.DocumentError as e:
        return jsonify({"error": str(e), "manual_entry_required": True}), 400
    try:
        reference = json.loads(request.form.get("reference") or "{}")
    except ValueError:
        reference = {}
    result["mismatches"] = document_service.consistency_check(result["fields"], reference, lang)
    return jsonify(result)


@bp.post("/documents/consistency")
def consistency():
    """JSON: {extracted: {...}, reference: {...}, lang} - re-run after the citizen corrects fields."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("extracted"), dict):
        return jsonify({"error": "extracted must be an object"}), 400
    return jsonify({"mismatches": document_service.consistency_check(
        body["extracted"], body.get("reference") or {}, body.get("lang", "en"))})


@bp.post("/packet")
def packet():
    """JSON: {scheme_id, provided_items: [doc ids], missing_fields: [field names]} -> PDF download."""
    body = request.get_json(silent=True) or {}
    scheme_id = body.get("scheme_id")
    if not isinstance(scheme_id, str):
        return jsonify({"error": "scheme_id is required"}), 400
    try:
        pdf = packet_service.build_packet(scheme_id, body.get("provided_items") or [],
                                          body.get("missing_fields") or [])
    except KeyError:
        return jsonify({"error": "Scheme not found"}), 404
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="janaseva_packet_{scheme_id}.pdf"'})
