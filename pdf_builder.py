"""Builds the task PDF: a cover page (logo, name, ID, doctor) + one page per solution image."""
import io
import os
import re

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
FONTS = os.path.join(HERE, "fonts") + os.sep
DEJAVU = "/usr/share/fonts/truetype/dejavu/"
FONT_SETS = [
    (FONTS + "NotoSansArabic-Regular.ttf", FONTS + "NotoSansArabic-Bold.ttf",
     FONTS + "NotoSans-Regular.ttf", FONTS + "NotoSans-Bold.ttf"),  # bundled (PythonAnywhere)
    (NOTO + "NotoSansArabic-Regular.ttf", NOTO + "NotoSansArabic-Bold.ttf",
     NOTO + "NotoSans-Regular.ttf", NOTO + "NotoSans-Bold.ttf"),  # server (Docker + fonts-noto-core)
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf",
     r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),  # Windows laptop
    (DEJAVU + "DejaVuSans.ttf", DEJAVU + "DejaVuSans-Bold.ttf",
     DEJAVU + "DejaVuSans.ttf", DEJAVU + "DejaVuSans-Bold.ttf"),  # any Linux fallback
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
for _n, _b in (("Serif", "Body"), ("SerifB", "Bold")):  # Arabic glyphs inside serif text
    pdfmetrics.registerFont(TTFont(_n, pdfmetrics.getFont(_b).face.filename))
LATIN = {"Serif": "Times-Roman", "SerifB": "Times-Bold"}
BLUE = (0.18, 0.45, 0.71)  # Word's classic heading blue
BLACK = (0, 0, 0)


def _runs(visual, font):
    """Split already-reordered text into (chunk, font) runs: Arabic letters use the Arabic
    font, everything else (Latin, digits, punctuation) the Latin font."""
    out = []
    for ch in visual:
        f = font if ARABIC.match(ch) else LATIN.get(font, font + "L")
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


def _logo_row(c, logos, top, max_h=150, gap=26):
    """Up to 3 logos side by side, centred at the top. Returns the y below the row."""
    imgs = [_load_image(b, 900) for b in logos[:3]][::-1]  # RTL: first logo sent sits on the right
    W = A4[0]
    avail = W - 2 * 70
    n = len(imgs)
    box_w = min(190 if n == 1 else 160, (avail - gap * (n - 1)) / n)
    # scale each logo to the common height, but never wider than its box
    sizes = []
    for im in imgs:
        s = min(max_h / im.height, box_w / im.width)
        sizes.append((im.width * s, im.height * s))
    row_h = max(h for _, h in sizes)
    total = sum(w for w, _ in sizes) + gap * (n - 1)
    x = (W - total) / 2
    for im, (w, h) in zip(imgs, sizes):
        c.drawImage(_reader(im), x, top - row_h + (row_h - h) / 2, w, h)
        x += w + gap
    return top - row_h


def _frame(c, W, H, m):
    """Thin double frame (black outer, light grey inner) used on every page."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.6)
    c.roundRect(m, m, W - 2 * m, H - 2 * m, 10)
    c.setStrokeColorRGB(0.72, 0.72, 0.72)
    c.setLineWidth(0.6)
    c.roundRect(m + 5, m + 5, W - 2 * m - 10, H - 2 * m - 10, 7)


def build_pdf(out_path, name=None, student_id=None, images=(), logos=None, doctor=None,
              year=None, frame=False, title=None, logo=None):
    """images: list of raw bytes in the order the user sent them. Everything else is optional.
    logos: up to 3 images (bytes) shown centred on the cover; `logo` is the old single-logo arg."""
    logos = list(logos or ([] if logo is None else [logo]))
    W, H = A4
    c = canvas.Canvas(out_path, pagesize=A4)
    c.setTitle(" - ".join(x for x in (title, name, student_id) if x) or "PDF")
    if name:
        c.setAuthor(name)

    # ---------- cover ----------
    if frame:  # the frame choice covers the cover page too
        _frame(c, W, H, 18)

    rows = [(lbl, v) for lbl, v in (("By:", name), ("NO:", student_id), ("Submitted to:", doctor)) if v]
    lines = [ln.strip() for ln in (title or "").splitlines() if ln.strip()][:6]
    y = H - 70
    if logos:
        y = _logo_row(c, logos, y, max_h=130) - 50
    else:
        y -= 110 if (rows or lines) else 280

    # title: one centred line each (university / department / course / LAB#1 ...)
    for i, ln in enumerate(lines):
        size = 26 if len(ln) <= 14 else 20 if len(ln) <= 55 else 16
        y -= size * 0.4 if i else 0
        _centered(c, ln, y, "Serif", size, BLACK)
        y -= size + 12
    y -= 40 if lines else 0

    # By: / NO: / Submitted to: — label on its own line, value under it in blue
    for label, value in rows:
        _centered(c, label, y, "SerifB", 19, BLACK)
        y -= 32
        size = 22 if len(value) <= 34 else 17
        _centered(c, value, y, "Serif", size, BLUE)
        y -= 62

    if year:
        _centered(c, year, 70, "Serif", 18, BLACK)
    c.showPage()

    # ---------- solution pages ----------
    total = len(images)
    who = " — ".join(x for x in (name, student_id) if x)
    m = 30                       # page margin
    pad = 14 if frame else 0     # space between frame and picture
    footer = 34
    for i, data in enumerate(images, 1):
        img = _load_image(data)
        if frame:
            _frame(c, W, H, m - 12)
        _draw_fit(c, img, m + pad, m + footer, W - 2 * (m + pad), H - 2 * m - footer - pad)
        if not frame:
            c.setStrokeColorRGB(0.85, 0.82, 0.78)
            c.setLineWidth(0.6)
            c.line(m, m + footer - 10, W - m, m + footer - 10)
        c.setFillColorRGB(*MUTED)
        if who:
            draw_text(c, W - m - pad, m + 6, who, "Body", 10, "right")
        draw_text(c, m + pad, m + 6, f"{i} / {total}", "Body", 10)
        c.showPage()

    c.save()
    return out_path
