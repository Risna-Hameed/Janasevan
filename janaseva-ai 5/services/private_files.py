"""Private document storage. Files live outside /static under a random name and are
only returned through access-controlled API endpoints."""
import hashlib
import os
import secrets

from flask import current_app

MAX_BYTES = 5 * 1024 * 1024
SIGNATURES = [(b"%PDF-", "application/pdf"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg")]


class UploadError(ValueError):
    pass


def detect_type(data):
    """Trust the file's actual bytes, not its name or the browser's claimed type."""
    for sig, mime in SIGNATURES:
        if data.startswith(sig):
            return mime
    return None


def storage_dir(panchayat_id):
    path = os.path.join(current_app.config["PRIVATE_UPLOAD_DIR"], str(int(panchayat_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save(panchayat_id, filename, data):
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError("File is larger than 5 MB.")
    mime = detect_type(data)
    if not mime:
        raise UploadError("Only PDF, PNG or JPG files are accepted.")
    name = secrets.token_hex(16)                       # never derived from user input
    with open(os.path.join(storage_dir(panchayat_id), name), "wb") as fh:
        fh.write(data)
    safe_original = "".join(ch for ch in (filename or "document")[-80:] if ch.isalnum() or ch in "._- ") or "document"
    return {"storage_name": name, "original_name": safe_original, "mime_type": mime,
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read(panchayat_id, storage_name):
    if not storage_name.isalnum():                     # blocks ../ path tricks
        raise FileNotFoundError
    with open(os.path.join(storage_dir(panchayat_id), storage_name), "rb") as fh:
        return fh.read()
