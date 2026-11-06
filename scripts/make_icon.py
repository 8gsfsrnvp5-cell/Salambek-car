"""Генерирует значок программы (assets/autoservice.ico). Запускается разработчиком один раз."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "src" / "autoservice" / "assets" / "autoservice.ico"


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 5
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(37, 99, 235, 255))
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.46))
    except OSError:
        font = ImageFont.load_default()
    text = "AS"
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1] - size * 0.02), text, font=font, fill="white")
    d.rectangle([size * 0.2, size * 0.8, size * 0.8, size * 0.84], fill=(255, 255, 255, 220))
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    big = draw(256)
    big.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)
