"""Document intelligence (owned by P3).

1. OCR for ONE supported document type: an income certificate image.
2. Cross-document consistency check: compares extracted fields with the
   citizen's profile / application draft and flags mismatches.

Files are processed in memory and never written to disk.
Reading the text does NOT prove a document is authentic.
"""
import io
import re
from difflib import SequenceMatcher

from PIL import Image, UnidentifiedImageError

try:
    import pytesseract
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_BYTES = 5 * 1024 * 1024
AUTHENTICITY_NOTE = {
    "en": "Text was read automatically. This does not prove the document is genuine. Please check every field.",
    "ml": "ടെക്സ്റ്റ് സ്വയം വായിച്ചതാണ്. ഇത് രേഖ യഥാർത്ഥമാണെന്ന് തെളിയിക്കുന്നില്ല. എല്ലാ വിവരങ്ങളും പരിശോധിക്കുക.",
}


class DocumentError(ValueError):
    """Validation problem the API should return as HTTP 400."""


def validate_upload(filename, data):
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentError("Only PNG or JPG images are supported. You can also enter the details manually.")
    if not data:
        raise DocumentError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise DocumentError("File is larger than 5 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except (UnidentifiedImageError, OSError):
        raise DocumentError("The file is not a readable image.")


def _languages():
    try:
        langs = pytesseract.get_languages(config="")
    except Exception:
        return "eng"
    return "eng+mal" if "mal" in langs else "eng"


def run_ocr(data):
    """Return (text, confidence 0-100). Empty text if OCR is unavailable or unreadable."""
    if not OCR_AVAILABLE:
        return "", 0
    img = Image.open(io.BytesIO(data)).convert("L")
    if img.width < 1200:  # small photos read better when enlarged
        scale = 1200 / img.width
        img = img.resize((1200, int(img.height * scale)))
    lang = _languages()
    try:
        d = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img, lang=lang)
    except Exception:
        return "", 0
    confs = [float(c) for c, w in zip(d["conf"], d["text"]) if w.strip() and float(c) >= 0]
    return text, round(sum(confs) / len(confs)) if confs else 0


def _to_int(amount):
    digits = re.sub(r"[^\d]", "", amount or "")
    return int(digits) if digits else None


def parse_income_certificate(text):
    """Pull the fields we need from income-certificate text. Missing fields stay None."""
    flat = re.sub(r"[ \t]+", " ", text or "")
    fields = {"name": None, "annual_income": None, "village": None, "taluk": None, "issue_date": None}

    m = re.search(r"(?:certif\w*\s+that\s+(?:Sri|Smt|Shri|Kum|Mr|Ms)?\.?\s*)([A-Z][A-Za-z .]{2,40}?)(?:,|\s+(?:S/o|D/o|W/o|son|daughter|residing))", flat, re.I)
    if not m:
        m = re.search(r"Name\s*(?:of\s+(?:the\s+)?applicant)?\s*[:\-]\s*([A-Za-z .]{3,40})", flat, re.I)
    if m:
        fields["name"] = m.group(1).strip(" .")

    m = re.search(r"(?:annual\s+(?:family\s+)?income|income|വരുമാനം)[^\d\n]{0,40}?(?:Rs\.?|INR|₹)?\s*([\d,]{3,12})", flat, re.I)
    if m:
        fields["annual_income"] = _to_int(m.group(1))

    m = re.search(r"Village\s*[:\-]?\s*([A-Za-z]+)", flat, re.I)
    if m:
        fields["village"] = m.group(1)
    m = re.search(r"Taluk\s*[:\-]?\s*([A-Za-z]+)", flat, re.I)
    if m:
        fields["taluk"] = m.group(1)
    m = re.search(r"(\d{2}[/\-.]\d{2}[/\-.]\d{4})", flat)
    if m:
        fields["issue_date"] = m.group(1)
    return fields


def analyze(filename, data, doc_type="income_cert", lang="en"):
    """Validate, OCR and parse. Always returns a usable result (manual entry if OCR fails)."""
    validate_upload(filename, data)
    lang = lang if lang in ("en", "ml") else "en"
    if doc_type != "income_cert":
        return {"doc_type": doc_type, "ocr_used": False, "fields": {}, "confidence": 0,
                "manual_entry_required": True, "message": "Automatic reading is supported only for income certificates. Please enter details manually.",
                "note": AUTHENTICITY_NOTE[lang]}
    text, conf = run_ocr(data)
    fields = parse_income_certificate(text)
    found = {k: v for k, v in fields.items() if v is not None}
    manual = not found
    if not OCR_AVAILABLE:
        message = "Automatic reading is not available on this server. Please enter the details manually."
    elif manual:
        message = "We could not read this document clearly. Please enter the details manually or upload a clearer photo."
    else:
        message = "Please review the details we read and correct anything that is wrong."
    return {"doc_type": doc_type, "ocr_used": OCR_AVAILABLE, "fields": fields, "confidence": conf,
            "low_confidence": conf < 60, "manual_entry_required": manual, "message": message,
            "note": AUTHENTICITY_NOTE[lang]}


def _norm_name(n):
    n = re.sub(r"\b(sri|smt|shri|kum|mr|ms|mrs|dr)\b\.?", "", (n or "").lower())
    return re.sub(r"[^a-z ]", "", n).split()


def consistency_check(extracted, reference, lang="en"):
    """Compare extracted document fields with profile/draft values. Returns a list of mismatches."""
    ml = lang == "ml"
    issues = []
    reference = reference or {}
    a, b = extracted.get("name"), reference.get("name")
    if a and b:
        ratio = SequenceMatcher(None, " ".join(sorted(_norm_name(a))), " ".join(sorted(_norm_name(b)))).ratio()
        if ratio < 0.85:
            issues.append({"field": "name", "document_value": a, "entered_value": b, "severity": "high",
                           "message": "പേര് രേഖയിലും അപേക്ഷയിലും വ്യത്യസ്തമാണ്. അപേക്ഷ നിരസിക്കപ്പെടാം." if ml else
                           "The name on the document does not match the name in your application. Mismatched names are a common reason for rejection."})
    a, b = extracted.get("annual_income"), reference.get("annual_income")
    if a is not None and b not in (None, ""):
        try:
            b = int(b)
        except (TypeError, ValueError):
            b = None
        if b is not None and a != b:
            issues.append({"field": "annual_income", "document_value": a, "entered_value": b, "severity": "high",
                           "message": "രേഖയിലെ വരുമാനം നിങ്ങൾ നൽകിയ വരുമാനവുമായി പൊരുത്തപ്പെടുന്നില്ല." if ml else
                           "The income on the certificate is different from the income you entered. Use the certificate value."})
    for field in ("name", "annual_income"):
        if extracted.get(field) is None and reference.get(field) not in (None, ""):
            issues.append({"field": field, "document_value": None, "entered_value": reference.get(field), "severity": "low",
                           "message": "ഈ വിവരം രേഖയിൽ നിന്ന് വായിക്കാനായില്ല; സ്വയം പരിശോധിക്കുക." if ml else
                           "This field could not be read from the document. Please check it yourself."})
    return issues
