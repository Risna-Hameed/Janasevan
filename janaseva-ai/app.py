"""JANASEVA AI - main entry point (shared file; written once, changed only together).

Run locally:   python app.py        then open http://127.0.0.1:5000
Production:    gunicorn app:app
"""
import os

from flask import Flask, jsonify, render_template, request

from config import Config
from database.db import init_db
from routes.application_routes import bp as application_bp   # P1
from routes.citizen_verification_routes import bp as citizen_verification_bp   # Panchayat module (citizen side)
from routes.panchayat_records_routes import bp as panchayat_records_bp         # Panchayat module
from routes.panchayat_routes import bp as panchayat_bp, page_bp as panchayat_page_bp
from routes.chat_routes import bp as chat_bp                  # P3
from routes.document_routes import bp as document_bp          # P3
from routes.recommend_routes import bp as recommend_bp        # P3 (eligibility + BestFit)
from routes.scheme_routes import bp as scheme_bp              # P1
from services import document_service


def create_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    app.json.ensure_ascii = False
    init_db(app)
    if app.config.get("SEED_DEMO"):
        from database.db import connect
        from database.seed_panchayat import seed
        with app.app_context():
            conn = connect(app.config["DATABASE"])
            try:
                seed(conn)               # fictional data, only if no demo panchayat exists yet
            finally:
                conn.close()

    for bp in (scheme_bp, application_bp, chat_bp, document_bp, recommend_bp):
        app.register_blueprint(bp, url_prefix="/api")
    app.register_blueprint(citizen_verification_bp, url_prefix="/api/citizen")
    app.register_blueprint(panchayat_bp, url_prefix="/api/panchayat")
    app.register_blueprint(panchayat_records_bp, url_prefix="/api/panchayat")
    app.register_blueprint(panchayat_page_bp)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/panchayat") or request.path.startswith("/api/citizen"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # asset_version changes on every start, so browsers fetch fresh CSS/JS instead of an old cached copy
    import time as _time
    asset_version = str(int(_time.time()))

    @app.context_processor
    def inject_asset_version():
        return {"asset_version": asset_version}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "ocr_available": document_service.OCR_AVAILABLE,
                        "llm_configured": bool(os.environ.get("LLM_API_KEY", "").strip())})

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        if request.path.startswith("/static/"):
            return "Not found", 404
        if request.path.startswith("/panchayat"):
            return render_template("panchayat.html"), 200
        return render_template("index.html"), 200

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File is larger than 5 MB.", "manual_entry_required": True}), 413

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
