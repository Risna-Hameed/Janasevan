"""Akshaya Centre handoff packet (owned by P3).

A one-page printable PDF the citizen can carry to an Akshaya centre:
scheme name, document checklist with ticks, missing items, next steps
and a QR code that opens the OFFICIAL portal. No personal data goes
into the QR code. The PDF is in English because the built-in PDF
fonts cannot render Malayalam script correctly.
"""
import io
from datetime import date

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from services import retrieval

NAVY = colors.HexColor("#14325c")
GREEN = colors.HexColor("#1e7a46")
RED = colors.HexColor("#b3261e")
AMBER = colors.HexColor("#9a6700")


def _wrap(c, text, x, y, width, font="Helvetica", size=10, leading=13):
    c.setFont(font, size)
    words, line = (text or "").split(), ""
    for w in words:
        trial = (line + " " + w).strip()
        if c.stringWidth(trial, font, size) > width:
            c.drawString(x, y, line)
            y -= leading
            line = w
        else:
            line = trial
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_packet(scheme_id, provided_items=None, missing_fields=None):
    """Return PDF bytes. Raises KeyError if the scheme does not exist."""
    scheme = retrieval.get_scheme(scheme_id)
    if not scheme:
        raise KeyError(scheme_id)
    provided = set(provided_items or [])
    missing_fields = missing_fields or []
    docs = scheme.get("required_documents", [])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x, y = 18 * mm, H - 20 * mm

    c.setFillColor(NAVY)
    c.rect(0, H - 30 * mm, W, 30 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(x, H - 15 * mm, "JANASEVA AI - Application Packet")
    c.setFont("Helvetica", 9)
    c.drawString(x, H - 22 * mm, f"Prepared on {date.today().isoformat()}  |  Take this to your Akshaya centre or use it while applying online")

    y = H - 40 * mm
    c.setFillColor(colors.black)
    if scheme.get("is_test"):
        c.setFillColor(RED)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y, "DEMO ONLY - fictional test scheme, not a real government scheme")
        y -= 7 * mm
        c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    y = _wrap(c, scheme.get("name_en", ""), x, y, 120 * mm, "Helvetica-Bold", 14, 17)
    y = _wrap(c, f"Department: {scheme.get('department') or 'Needs verification'}", x, y - 2, 120 * mm)
    fresh = retrieval.freshness(scheme)
    y = _wrap(c, f"Source status: {fresh['label_en']}", x, y, 120 * mm)
    deadline = scheme.get("deadline") or "No verified deadline - confirm with the department"
    y = _wrap(c, f"Deadline: {deadline}", x, y, 120 * mm)

    # QR code to the official portal only (no personal data)
    url = scheme.get("official_url")
    if url:
        qr = QrCodeWidget(url)
        b = qr.getBounds()
        size = 38 * mm
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, W - 18 * mm - size, H - 78 * mm)
        c.setFont("Helvetica", 7)
        c.drawCentredString(W - 18 * mm - size / 2, H - 82 * mm, "Scan: official portal")
    else:
        c.setFont("Helvetica-Oblique", 8)
        c.drawRightString(W - 18 * mm, H - 50 * mm, "Official link needs verification")

    y = min(y, H - 90 * mm) - 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(NAVY)
    c.drawString(x, y, "Document checklist")
    y -= 7 * mm
    if not docs:
        c.setFillColor(AMBER)
        y = _wrap(c, "The document list for this scheme has not been verified. Ask at the centre or check the official portal.", x, y, 170 * mm)
    for doc in docs:
        have = doc["id"] in provided
        c.setFillColor(GREEN if have else (RED if doc.get("mandatory") else AMBER))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y, "[x]" if have else "[  ]")
        c.setFillColor(colors.black)
        label = doc.get("name_en", doc["id"]) + ("  (mandatory)" if doc.get("mandatory") else "  (if required)")
        c.setFont("Helvetica", 10)
        c.drawString(x + 10 * mm, y, label + ("" if have else "  - MISSING"))
        y -= 6 * mm

    if missing_fields:
        y -= 3 * mm
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(NAVY)
        c.drawString(x, y, "Information still needed")
        y -= 6 * mm
        c.setFillColor(colors.black)
        for f in missing_fields:
            y = _wrap(c, "- " + str(f).replace("_", " "), x, y, 170 * mm)

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(NAVY)
    c.drawString(x, y, "Next steps")
    y -= 6 * mm
    c.setFillColor(colors.black)
    steps = [
        "Collect every item marked MISSING above.",
        "Check that your name and income match on every document.",
        f"Apply through the official process: {scheme.get('application_method') or 'needs verification'}.",
        "Keep the acknowledgement number you receive from the official portal.",
    ]
    for i, s in enumerate(steps, 1):
        y = _wrap(c, f"{i}. {s}", x, y, 170 * mm)

    c.setFillColor(colors.HexColor("#555555"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(x, 15 * mm, "JANASEVA AI is an independent assistance prototype. Verify all information with the official department.")
    c.drawString(x, 11 * mm, "This packet is not an application and does not guarantee eligibility or approval.")
    c.showPage()
    c.save()
    return buf.getvalue()
