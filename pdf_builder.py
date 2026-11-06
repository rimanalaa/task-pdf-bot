"""Builds the task PDF: a cover page (logo, name, ID, doctor) + one page per solution image."""
import io
import os
import re
from datetime import date

import arabic_reshaper
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:  # python-bidi >= 0.5
    from bidi import get_display
except ImportError:  # older python-bidi
    from bidi.algorithm import get_display

HERE = os.path.dirname(os.path.abspath(__file__))
NOTO = "/usr/share/fonts/truetype/noto/"
# (arabic regular, arabic bold, latin regular, latin bold) — first set that exists wins
FONT_SETS = [
    (NOTO + "NotoSansArabic-Regular.ttf", NOTO + "NotoSansArabic-Bold.ttf",
     NOTO + "NotoSans-Regular.ttf", NOTO + "NotoSans-Bold.ttf"),  # server (Docker + fonts-noto-core)
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf",
     r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),  # Windows laptop
]
ARABIC = re.compile("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

INK = (0.17, 0.15, 0.13)
ACCENT = (0.69, 0.53, 0.31)
MUTED = (0.45, 0.43, 0.40)


def _register_fonts():
    for a, ab, l, lb in FONT_SETS:
        if all(os.path.exists(f) for f in (a, ab, l, lb)):
            for name, f in (("Body", a), ("Bold", ab), ("BodyL", l), ("BoldL", lb)):
                pdfmetrics.registerFont(TTFont(name, f))
            return
    raise RuntimeError("No Arabic + Latin fonts found (install fonts-noto-core)")


_register_fonts()


def _runs(visual, font):
    """Split already-reordered text into (chunk, font) runs: Arabic letters use the Arabic
    font, everything else (Latin, digits, punctuation) the Latin font."""
    out = []
    for ch in visual:
        f = font if ARABIC.match(ch) else font + "L"
        if ch == " " and out:
            f = out[-1][1]
        if out and out[-1][1] == f:
            out[-1][0] += ch
        else:
            out.append([ch, f])
    return out


def _width(visual, font, size):
    return sum(pdfmetrics.stringWidth(t, f, size) for t, f in _runs(visual, font))


def draw_text(c, x, y, text, font, size, align="left"):
    vis = ar(text)
    w = _width(vis, font, size)
    x = {"left": x, "center": x - w / 2, "right": x - w}[align]
    for t, f in _runs(vis, font):
        c.setFont(f, size)
        c.drawString(x, y, t)
        x += pdfmetrics.stringWidth(t, f, size)


def ar(text: str) -> str:
    """Shape + reorder Arabic so it renders correctly (works for English too)."""
    return get_display(arabic_reshaper.reshape(text or ""))


def _load_image(data: bytes, max_side: int = 2200) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # phone photos: fix rotation
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def _reader(img: Image.Image) -> ImageReader:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    buf.seek(0)
    return ImageReader(buf)


def _draw_fit(c, img, x, y, w, h):
    """Draw img inside box (x, y, w, h) keeping aspect ratio, centred."""
    iw, ih = img.size
    s = min(w / iw, h / ih)
    dw, dh = iw * s, ih * s
    c.drawImage(_reader(img), x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)


def _centered(c, text, y, font, size, color):
    c.setFillColorRGB(*color)
    draw_text(c, A4[0] / 2, y, text, font, size, "center")


def build_pdf(out_path, name, student_id, images, logo=None, doctor=None, title="حل التاسك"):
    """images: list of raw bytes, already in the order the user sent them."""
    W, H = A4
    c = canvas.Canvas(out_path, pagesize=A4)
    c.setTitle(f"{title} - {name} - {student_id}")
    c.setAuthor(name)

    # ---------- cover ----------
    c.setStrokeColorRGB(*ACCENT)
    c.setLineWidth(2)
    c.roundRect(28, 28, W - 56, H - 56, 14)

    y = H - 90
    if logo:
        lg = _load_image(logo, 900)
        box = 190
        _draw_fit(c, lg, (W - box) / 2, y - box, box, box)
        y -= box + 60
    else:
        y -= 120

    _centered(c, title, y, "Bold", 34, INK)
    y -= 26
    c.setStrokeColorRGB(*ACCENT)
    c.setLineWidth(1.5)
    c.line(W / 2 - 70, y, W / 2 + 70, y)
    y -= 70

    rows = [("الاسم", name), ("الرقم الجامعي", student_id)]
    if doctor:
        rows.append(("الدكتور", doctor))
    for label, value in rows:
        _centered(c, label, y, "Body", 15, MUTED)
        y -= 30
        _centered(c, value, y, "Bold", 22, INK)
        y -= 56

    _centered(c, date.today().strftime("%Y-%m-%d"), 70, "Body", 12, MUTED)
    c.showPage()

    # ---------- solution pages ----------
    total = len(images)
    margin, footer = 30, 34
    for i, data in enumerate(images, 1):
        img = _load_image(data)
        _draw_fit(c, img, margin, margin + footer, W - 2 * margin, H - 2 * margin - footer)
        c.setStrokeColorRGB(0.85, 0.82, 0.78)
        c.setLineWidth(0.6)
        c.line(margin, margin + footer - 10, W - margin, margin + footer - 10)
        c.setFillColorRGB(*MUTED)
        draw_text(c, W - margin, margin + 6, f"{name} — {student_id}", "Body", 10, "right")
        draw_text(c, margin, margin + 6, f"{i} / {total}", "Body", 10)
        c.showPage()

    c.save()
    return out_path
