import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import django
django.setup()

from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont

from apps.core.management.commands.seed_demo import PRODUCTS, PALETTE


def load_font(size):
    candidates = [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main():
    output = Path(__file__).resolve().parent / "products"
    output.mkdir(parents=True, exist_ok=True)
    font = load_font(42)
    for index, (name, *_rest) in enumerate(PRODUCTS):
        image = Image.new("RGB", (800, 600), PALETTE[index % len(PALETTE)])
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 780, 580), outline="#101820", width=4)
        words = name.split()
        lines = []
        line = ""
        for word in words:
            candidate = (line + " " + word).strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] > 650:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        y = (600 - len(lines) * 52) // 2
        for text_line in lines:
            box = draw.textbbox((0, 0), text_line, font=font)
            draw.text(((800 - (box[2] - box[0])) / 2, y), text_line, fill="#101820", font=font)
            y += 52
        image.save(output / (slugify(name, allow_unicode=True) + ".png"), format="PNG")


if __name__ == "__main__":
    main()
