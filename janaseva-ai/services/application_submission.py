"""Pre-submission check and application PDFs (preview + final).

The check tells the student EXACTLY what is stopping submission:
  * a profile/application field that is empty,
  * a mandatory document they have not marked as available,
  * an eligibility condition that is not met or cannot be checked yet.
Submission here is a DEMO: nothing is sent to any government department.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from services.eligibility_engine import check_eligibility

FIELD_LABELS = {
    "name": ("Full name (as on certificates)", "പൂർണ്ണ പേര് (സർട്ടിഫിക്കറ്റിലുള്ളതുപോലെ)"),
    "state": ("State", "സംസ്ഥാനം"), "district": ("District", "ജില്ല"),
    "education_level": ("Education level", "വിദ്യാഭ്യാസ നില"), "course": ("Current course", "നിലവിലെ കോഴ്സ്"),
    "institution_name": ("Institution name", "സ്ഥാപനത്തിന്റെ പേര്"), "institution_type": ("Institution type", "സ്ഥാപന തരം"),
    "annual_income": ("Annual family income (Rs.)", "വാർഷിക കുടുംബ വരുമാനം (₹)"),
    "year_of_study": ("Year of study", "പഠന വർഷം"), "course_mode": ("Course type", "കോഴ്സ് തരം"),
    "institution_aicte_approved": ("Institution AICTE-approved", "സ്ഥാപനം AICTE അംഗീകൃതമാണോ"),
    "previous_exam_percentage": ("Marks in previous exam (%)", "മുൻ പരീക്ഷയിലെ മാർക്ക് (%)"),
    "class12_percentile": ("Class 12 percentile", "പ്ലസ് ടു പെർസന്റൈൽ"), "gender": ("Gender", "ലിംഗം"),
    "community": ("Community", "സമുദായം"), "disability_percent": ("Disability percentage", "ഭിന്നശേഷി ശതമാനം"),
    "bpl": ("BPL family", "ബിപിഎൽ കുടുംബം"), "receives_other_scholarship": ("Already receives another scholarship", "മറ്റൊരു സ്കോളർഷിപ്പ് ലഭിക്കുന്നുണ്ടോ"),
}


def label(field, lang="en"):
    en, ml = FIELD_LABELS.get(field, (field.replace("_", " ").capitalize(), field))
    return ml if lang == "ml" else en


def check(scheme, form, provided_items, profile=None):
    """Return {ready, blockers[], warnings[]}. Every blocker says what is wrong and what to do."""
    blockers, warnings, seen = [], [], set()
    if not scheme.get("eligibility_rules"):
        # rules and document list not verified yet - we cannot prepare an honest application for this scheme
        return {"ready": False, "warnings": [], "eligibility_status": "verification_required", "blockers": [{
            "type": "eligibility", "field": "scheme_rules", "action": "eligibility",
            "title_en": "This scheme's rules are not verified yet", "title_ml": "ഈ പദ്ധതിയുടെ നിബന്ധനകൾ ഇതുവരെ പരിശോധിച്ചിട്ടില്ല",
            "detail_en": "JANASEVA AI cannot prepare an application until the official rules and document list are added. Use the official portal.",
            "detail_ml": "ഔദ്യോഗിക നിബന്ധനകളും രേഖകളുടെ പട്ടികയും ചേർക്കുന്നതുവരെ അപേക്ഷ തയ്യാറാക്കാനാവില്ല. ഔദ്യോഗിക പോർട്ടൽ ഉപയോഗിക്കുക."}]}
    merged = {**(profile or {}), **{k: v for k, v in (form or {}).items() if v not in (None, "")}}
    provided = set(provided_items or [])

    elig = check_eligibility(merged, scheme)
    for item in elig["not_met"]:
        cf = next((c for c in elig["counterfactuals"] if c["rule_id"] == item["rule_id"]), {})
        blockers.append({"type": "eligibility", "field": item["field"],
                         "title_en": "Eligibility condition not met", "title_ml": "അർഹതാ നിബന്ധന പാലിക്കുന്നില്ല",
                         "detail_en": f"{item['description_en']} {cf.get('text_en', '')}".strip(),
                         "detail_ml": f"{item['description_ml']} {cf.get('text_ml', '')}".strip(),
                         "action": "eligibility"})
        seen.add(item["field"])
    for field, value in (form or {}).items():
        if value in (None, "") and field not in seen:
            seen.add(field)
            blockers.append({"type": "field", "field": field,
                             "title_en": f"Missing: {label(field)}", "title_ml": f"ബാക്കി: {label(field, 'ml')}",
                             "detail_en": "Fill this in the application details above.",
                             "detail_ml": "മുകളിലെ അപേക്ഷാ വിവരങ്ങളിൽ ഇത് പൂരിപ്പിക്കുക.", "action": "field"})
    for item in elig["unknown"]:
        if item["field"] not in seen:
            seen.add(item["field"])
            blockers.append({"type": "field", "field": item["field"],
                             "title_en": f"Missing: {label(item['field'])}", "title_ml": f"ബാക്കി: {label(item['field'], 'ml')}",
                             "detail_en": f"Needed to check: {item['description_en']}", "detail_ml": f"പരിശോധിക്കാൻ ആവശ്യം: {item['description_ml']}",
                             "action": "field"})
    for d in scheme.get("required_documents", []):
        if d.get("mandatory") and d["id"] not in provided:
            blockers.append({"type": "document", "field": d["id"],
                             "title_en": f"Document missing: {d['name_en']}", "title_ml": f"രേഖ ബാക്കി: {d['name_ml']}",
                             "detail_en": "Get this document and tick it in the BenefitIQ document checklist.",
                             "detail_ml": "ഈ രേഖ തയ്യാറാക്കി BenefitIQ രേഖാ പട്ടികയിൽ ടിക്ക് ചെയ്യുക.", "action": "document"})
    if not scheme.get("required_documents"):
        warnings.append({"en": "The document list for this scheme is not verified. Check the official source before applying.",
                         "ml": "ഈ പദ്ധതിയുടെ രേഖകളുടെ പട്ടിക പരിശോധിച്ചിട്ടില്ല. അപേക്ഷിക്കും മുമ്പ് ഔദ്യോഗിക സ്രോതസ്സ് പരിശോധിക്കുക."})
    if elig["status"] == "verification_required":
        warnings.append({"en": "This scheme's rules are not yet officially verified. The result may change after official verification.",
                         "ml": "ഈ പദ്ധതിയുടെ നിബന്ധനകൾ ഔദ്യോഗികമായി പരിശോധിച്ചിട്ടില്ല. ഫലം മാറാം."})
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings, "eligibility_status": elig["status"]}


# ---------------------------------------------------------------- PDF
NAVY = colors.HexColor("#14325c")


def _wrap(c, text, x, y, width, size=10, font="Helvetica", leading=None):
    leading = leading or size + 3
    c.setFont(font, size)
    line = ""
    for w in str(text or "").split():
        t = (line + " " + w).strip()
        if c.stringWidth(t, font, size) > width:
            c.drawString(x, y, line); y -= leading; line = w
        else:
            line = t
    if line:
        c.drawString(x, y, line); y -= leading
    return y


def _ascii(v):
    """Built-in PDF fonts cannot draw Malayalam; keep the PDF readable and say so."""
    s = "" if v is None else str(v)
    return s if all(ord(ch) < 256 for ch in s) else "(entered in Malayalam - see app)"


def _show(field, v):
    if v is None or v == "":
        return "NOT PROVIDED"
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    if field == "annual_income":
        try:
            return f"Rs. {int(v):,}"
        except (TypeError, ValueError):
            pass
    return _ascii(v)


def build_pdf(draft, scheme, kind, verifications=None):
    """kind = 'preview' (watermarked, not submitted) or 'final' (demo submitted copy)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x, right = 18 * mm, W - 18 * mm
    page = [1]

    def header():
        c.setFillColor(NAVY); c.rect(0, H - 26 * mm, W, 26 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 15)
        c.drawString(x, H - 13 * mm, "Scholarship Application" + (" - PREVIEW" if kind == "preview" else ""))
        c.setFont("Helvetica", 8.5)
        c.drawString(x, H - 20 * mm, "Prepared with JANASEVA AI (independent prototype) - not an official government form")
        c.drawRightString(right, H - 20 * mm, f"Page {page[0]}")
        # diagonal watermark
        c.saveState()
        c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.10) if kind == "preview" else colors.Color(0.1, 0.3, 0.6, alpha=0.08))
        c.setFont("Helvetica-Bold", 54 if kind == "preview" else 46)
        c.translate(W / 2, H / 2); c.rotate(35)
        c.drawCentredString(0, 0, "PREVIEW - NOT SUBMITTED" if kind == "preview" else "DEMO APPLICATION")
        c.restoreState()
        c.setFillColor(colors.black)
        return H - 36 * mm

    def need(y, space):
        if y < 25 * mm + space:
            footer(); c.showPage(); page[0] += 1
            return header()
        return y

    def footer():
        c.setFont("Helvetica-Oblique", 7.5); c.setFillColor(colors.HexColor("#666666"))
        c.drawString(x, 12 * mm, "DEMO APPLICATION ONLY - JANASEVA AI does not submit applications to any government department.")
        c.drawString(x, 8.5 * mm, "Apply through the official portal or office of the responsible authority.")
        c.setFillColor(colors.black)

    def section(y, title):
        y = need(y, 14 * mm)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 11.5); c.drawString(x, y, title)
        c.setStrokeColor(colors.HexColor("#dde3ec")); c.line(x, y - 2 * mm, right, y - 2 * mm)
        c.setFillColor(colors.black)
        return y - 7 * mm

    y = header()
    if scheme.get("is_test"):
        c.setFillColor(colors.HexColor("#b3261e")); c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, "FICTIONAL TEST SCHEME - for demonstration only"); y -= 6 * mm; c.setFillColor(colors.black)

    # reference block
    rows = [("Scheme", _ascii(scheme.get("name_en"))), ("Department", _ascii(scheme.get("department"))),
            ("Draft number", f"#{draft['id']}  (version {draft['version']})")]
    if kind == "final":
        rows.insert(0, ("Application reference", draft["application_ref"]))
        rows.append(("Submitted (demo) on", _fmt(draft["submitted_at"])))
    else:
        rows.append(("Status", "Preview - NOT submitted. Review every field before you submit."))
    for k, v in rows:
        c.setFont("Helvetica-Bold", 9.5); c.drawString(x, y, k)
        y = _wrap(c, v, x + 48 * mm, y, right - x - 48 * mm, 9.5) - 1

    y = section(y - 3 * mm, "1. Applicant details")
    for field, value in draft["form_data"].items():
        y = need(y, 8 * mm)
        missing = value in (None, "")
        c.setFont("Helvetica", 9.5); c.setFillColor(colors.HexColor("#444444")); c.drawString(x, y, label(field))
        c.setFillColor(colors.HexColor("#b3261e") if missing else colors.black)
        y = _wrap(c, _show(field, value), x + 70 * mm, y, right - x - 70 * mm, 9.5, "Helvetica-Bold" if missing else "Helvetica") - 1.5
        c.setFillColor(colors.black)

    y = section(y - 3 * mm, "2. Documents")
    docs = scheme.get("required_documents", [])
    provided = set(draft["provided_items"])
    if not docs:
        y = _wrap(c, "Document list for this scheme is not verified - check the official source.", x, y, right - x, 9.5)
    for d in docs:
        y = need(y, 7 * mm)
        have = d["id"] in provided
        c.setFillColor(colors.HexColor("#1e7a46") if have else colors.HexColor("#b3261e"))
        c.setFont("Helvetica-Bold", 9.5); c.drawString(x, y, "[x]" if have else "[  ]")
        c.setFillColor(colors.black); c.setFont("Helvetica", 9.5)
        c.drawString(x + 9 * mm, y, f"{d['name_en']}{' (mandatory)' if d.get('mandatory') else ''}{'' if have else ' - NOT AVAILABLE'}")
        y -= 5.5 * mm

    if verifications:
        y = section(y - 3 * mm, "3. Panchayat document verification records")
        for v in verifications:
            y = need(y, 7 * mm)
            y = _wrap(c, f"{v['category']}: request {v['request_ref']}, record {v['receipt_ref']} (verified against panchayat records; not a certificate)",
                      x, y, right - x, 9) - 1

    notes = draft.get("change_notes") or []
    if notes:
        y = section(y - 3 * mm, f"{4 if verifications else 3}. Changes requested by the applicant")
        for n in notes:
            y = need(y, 8 * mm)
            y = _wrap(c, f"Version {n['version']} ({_fmt(n['at'])}): {_ascii(n['note'])}", x, y, right - x, 9) - 1

    y = section(y - 3 * mm, "Declaration")
    y = _wrap(c, "I declare that the information above is true to the best of my knowledge. I understand that eligibility and any "
                 "benefit are decided only by the responsible authority after official verification.", x, y, right - x, 9)
    y = need(y - 6 * mm, 14 * mm)
    c.setFont("Helvetica", 9.5); c.drawString(x, y, "Applicant: " + _ascii(draft["form_data"].get("name") or "________________"))
    c.drawRightString(right, y, "Date: " + (_fmt(draft["submitted_at"])[:11] if kind == "final" else "__________"))
    footer()
    c.showPage(); c.save()
    return buf.getvalue()


def _fmt(iso):
    """Show times in Indian Standard Time - the citizen's own clock."""
    if not iso:
        return "-"
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(iso).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    except Exception:
        try:
            from datetime import timedelta, timezone
            return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %I:%M %p IST")
        except ValueError:
            return iso
