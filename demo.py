"""Run this (F5 in VS Code) to build sample.pdf locally with the same code the bot uses."""
import io
import os

from PIL import Image, ImageDraw

from pdf_builder import build_pdf


def fake_page(n):
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    for y in range(200, 1650, 60):
        d.line([(100, y), (1140, y)], fill=(200, 210, 230), width=2)
    d.text((120, 120), f"Solution page {n}", fill="black")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.pdf")
build_pdf(
    out,
    name="Riman Alaa",
    student_id="220220333",
    doctor="Eng. Hashem M. Hejazy",
    year="2026",
    title="The Islamic University – Faculty of Engineering\nComputer Engineering Department\n"
          "Introduction to Computers Lab\nLAB#1",
    images=[fake_page(i) for i in (1, 2)],
    frame=True,
)
print("Built", out)
os.startfile(out)  # opens it in your PDF viewer
