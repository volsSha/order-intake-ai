"""Render the fictional order-form image attachments (deterministic, no randomness)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "requests" / "attachments"

FORMS = {
    "R11-order-form.png": {
        "title": "PURCHASE ORDER FORM",
        "rows": [
            ("Customer", "Pine Dental (fictional)"),
            ("Order reference", "O9"),
            ("Item / SKU", "CAB-2  USB-C cable 2 m"),
            ("Quantity (individual items)", "4"),
        ],
    },
}


def render(name: str, form: dict) -> None:
    canvas = Image.new("RGB", (900, 420), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.load_default(size=30)
    font = ImageFont.load_default(size=22)
    draw.rectangle((0, 0, 900, 80), fill="#e8eef6")
    draw.text((36, 22), form["title"], fill="black", font=title_font)
    y = 120
    for label, value in form["rows"]:
        draw.text((36, y), f"{label}:", fill="#333333", font=font)
        draw.text((400, y), value, fill="black", font=font)
        draw.line((36, y + 40, 864, y + 40), fill="#cccccc")
        y += 64
    canvas.save(OUT / name)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, form in FORMS.items():
        render(name, form)
        print(f"wrote {OUT / name}")
