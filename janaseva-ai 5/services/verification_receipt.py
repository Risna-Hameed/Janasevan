"""Verification record PDF. It records an administrative finding - it is NOT a certificate."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from services.verification_workflow import DOCUMENT_CATEGORIES

GREEN = colors.HexColor("#1f5e3b")


def _wrap(c, text, x, y, width, size=10, font="Helvetica"):
    c.setFont(font, size)
    line = ""
    for w in (text or "").split():
        t = (line + " " + w).strip()
        if c.stringWidth(t, font, size) > width:
            c.drawString(x, y, line); y -= size + 3; line = w
        else:
            line = t
    if line:
        c.drawString(x, y, line); y -= size + 3
    return y


def build(req, review, panchayat, block, ward, is_demo):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x = 18 * mm
    c.setFillColor(GREEN); c.rect(0, H - 28 * mm, W, 28 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 16)
    c.drawString(x, H - 14 * mm, "Document Verification Record")
    c.setFont("Helvetica", 9)
    c.drawString(x, H - 21 * mm, f"{panchayat} · {block} · issued through JANASEVA AI (prototype)")
    y = H - 40 * mm
    c.setFillColor(colors.black)
    if is_demo:
        c.setFillColor(colors.HexColor("#b3261e")); c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y, "DEMONSTRATION RECORD - fictional data, no legal validity"); y -= 8 * mm
        c.setFillColor(colors.black)
    cat = DOCUMENT_CATEGORIES.get(req["document_category"], {})
    rows = [("Record reference", review["receipt_ref"]), ("Request reference", req["request_ref"]),
            ("Document type", cat.get("name_en", req["document_category"])), ("Ward", ward),
            ("Purpose stated by citizen", req["purpose"]), ("Outcome", "Details checked and found consistent with panchayat records"),
            ("Official reference", review["official_reference"] or "-"), ("Recorded on", review["created_at"][:10])]
    for k, v in rows:
        c.setFont("Helvetica-Bold", 10); c.drawString(x, y, k)
        y = _wrap(c, str(v), x + 55 * mm, y, 115 * mm) - 2
    if review["remarks"]:
        y -= 3 * mm
        c.setFont("Helvetica-Bold", 10); c.drawString(x, y, "Remarks"); y -= 5 * mm
        y = _wrap(c, review["remarks"], x, y, 170 * mm)
    y -= 6 * mm
    c.setFillColor(colors.HexColor("#444444"))
    for line in [
        "What this record means: a panchayat official compared the submitted document with the panchayat's",
        "household survey records and recorded that the checked details are consistent.",
        "What it does NOT mean: it is not a new certificate, it does not certify income, community or any",
        "legal status, and it is not approval of any scholarship, grant or benefit.",
        "Apply for the benefit through the official process of the responsible authority.",
    ]:
        c.setFont("Helvetica", 9); c.drawString(x, y, line); y -= 4.5 * mm
    c.showPage(); c.save()
    return buf.getvalue()
