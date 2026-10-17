"""Creates a FICTIONAL income certificate image for the demo (no real person)."""
import os
from PIL import Image, ImageDraw, ImageFont

def font(size, bold=False):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
              "C:/Windows/Fonts/arial%s.ttf" % ("bd" if bold else ""), "/Library/Fonts/Arial.ttf"]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def make(path, name="Anu Demo Kumar", income="1,20,000"):
    img = Image.new("RGB", (1400, 1000), "white")
    d = ImageDraw.Draw(img)
    d.text((380, 60), "SAMPLE INCOME CERTIFICATE", fill="black", font=font(40, True))
    d.text((440, 120), "FICTIONAL - FOR DEMO ONLY", fill="red", font=font(30, True))
    lines = [
        f"This is to certify that Sri {name}, S/o Demo Father,",
        "residing at Village: Kuttippuram, Taluk: Tirur,",
        f"has an Annual Family Income of Rs. {income} only.",
        "Issued on: 16/10/2026",
        "Certificate No: DEMO-0001",
    ]
    y = 260
    for line in lines:
        d.text((90, y), line, fill="black", font=font(34))
        y += 80
    img.save(path)

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    make(os.path.join(here, "sample_income_certificate.png"))
    make(os.path.join(here, "sample_income_certificate_mismatch.png"), name="Anju Varghese", income="3,40,000")
    print("Samples written to", here)
