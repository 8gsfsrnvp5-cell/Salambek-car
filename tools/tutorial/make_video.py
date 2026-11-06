"""Сборка видеоурока: кадры → оформление → озвучка (голос Windows «Microsoft Pavel») → MP4.

python tools/tutorial/make_video.py <рабочая_папка> <итоговый.mp4> [--skip-capture]
Требуется Windows 10/11 с русским голосом Pavel (входит в языковой пакет). Интернет не нужен.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scenes import CH, SCENES  # noqa: E402

WORK = Path(sys.argv[1]).resolve()
OUT_MP4 = Path(sys.argv[2]).resolve()
SKIP_CAPTURE = "--skip-capture" in sys.argv
SHOTS = WORK / "shots"
FRAMES = WORK / "frames"
TTS = WORK / "tts"
for p in (FRAMES, TTS):
    p.mkdir(parents=True, exist_ok=True)

FW, FH = 1920, 1080
TOP, CAP = 72, 170
AREA = (0, TOP, FW, FH - CAP)  # область содержимого 1920×838
BG = (11, 18, 32)
ACCENT = (37, 99, 235)
HL = (239, 68, 68)
FONTS = Path(r"C:\Windows\Fonts")


def font(size, bold=False):
    return ImageFont.truetype(str(FONTS / ("segoeuib.ttf" if bold else "segoeui.ttf")), size)


def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=fnt) <= width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def chrome(img, idx, total, chapter, title, sub):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, FW, TOP], fill=(15, 23, 42))
    d.rectangle([0, TOP - 3, FW, TOP], fill=ACCENT)
    d.text((40, 18), "AutoService AI", font=font(28, True), fill="white")
    d.text((290, 22), f"Глава {chapter}. {CH[chapter]}", font=font(24), fill=(148, 163, 184))
    counter = f"{idx} / {total}"
    d.text((FW - 40 - d.textlength(counter, font=font(24)), 22), counter, font=font(24), fill=(148, 163, 184))
    y0 = FH - CAP
    d.rectangle([0, y0, FW, FH], fill=(15, 23, 42))
    d.rectangle([0, y0, 8, FH], fill=ACCENT)
    d.text((60, y0 + 22), title, font=font(40, True), fill="white")
    for i, line in enumerate(wrap(d, sub, font(28), FW - 120)[:2]):
        d.text((60, y0 + 80 + i * 38), line, font=font(28), fill=(203, 213, 225))


def fit(im, max_w, max_h, upscale=1.0):
    s = min(max_w / im.width, max_h / im.height, upscale)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS), s


def shadow_paste(canvas, im, x, y, radius=18):
    sh = Image.new("RGBA", (im.width + radius * 4, im.height + radius * 4), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle([radius * 2, radius * 2, radius * 2 + im.width, radius * 2 + im.height], fill=(0, 0, 0, 170))
    sh = sh.filter(ImageFilter.GaussianBlur(radius))
    canvas.paste(sh, (x - radius * 2 + 6, y - radius * 2 + 10), sh)
    canvas.paste(im, (x, y))


def draw_highlights(canvas, rects, scale, ox, oy):
    d = ImageDraw.Draw(canvas)
    for x, y, w, h in rects:
        pad = 6
        box = [ox + x * scale - pad, oy + y * scale - pad, ox + (x + w) * scale + pad, oy + (y + h) * scale + pad]
        d.rounded_rectangle(box, radius=10, outline=HL, width=4)


def render_shot(name, meta):
    m = meta[name]
    ax, ay, aw, ah = AREA[0], AREA[1], AREA[2] - AREA[0], AREA[3] - AREA[1]
    canvas = Image.new("RGB", (FW, FH), BG)
    shot = Image.open(SHOTS / f"{name}.png").convert("RGB")
    if m["kind"] == "window":
        im, s = fit(shot, aw, ah)
        x, y = ax + (aw - im.width) // 2, ay + (ah - im.height) // 2
        canvas.paste(im, (x, y))
        draw_highlights(canvas, m["highlights"], s, x, y)
    elif m["kind"] == "pdf":
        canvas.paste((71, 85, 105), [ax, ay, ax + aw, ay + ah])
        page = shot
        if page.height > ah - 30:
            page = page.crop((0, 0, page.width, int((ah - 30) * page.width / min(page.width, 1100))))
        im, _ = fit(page, aw - 200, ah - 30)
        shadow_paste(canvas, im, ax + (aw - im.width) // 2, ay + 15)
    else:  # dialog поверх затемнённого окна
        if m.get("base"):
            base, _ = fit(Image.open(SHOTS / m["base"]).convert("RGB"), aw, ah)
            bx, by = ax + (aw - base.width) // 2, ay + (ah - base.height) // 2
            canvas.paste(base, (bx, by))
            dark = Image.new("RGBA", (aw, ah), (2, 6, 23, 150))
            canvas.paste(dark, (ax, ay), dark)
        else:
            canvas.paste((30, 41, 59), [ax, ay, ax + aw, ay + ah])
        im, s = fit(shot, aw - 120, ah - 60, upscale=1.45)
        x, y = ax + (aw - im.width) // 2, ay + (ah - im.height) // 2
        shadow_paste(canvas, im, x, y)
        draw_highlights(canvas, m["highlights"], s, x, y)
    return canvas


def render_card(c):
    canvas = Image.new("RGB", (FW, FH), BG)
    d = ImageDraw.Draw(canvas)
    ax, ay, aw, ah = AREA[0], AREA[1], AREA[2] - AREA[0], AREA[3] - AREA[1]
    for i in range(ah):
        t = i / ah
        d.line([(0, ay + i), (FW, ay + i)], fill=(int(15 + 10 * t), int(23 + 30 * t), int(42 + 60 * t)))
    y = ay + 90
    if c.get("kicker"):
        d.text((160, y), c["kicker"].upper(), font=font(28, True), fill=(96, 165, 250))
        y += 60
    d.text((160, y), c["title"], font=font(72, True), fill="white")
    y += 130
    for line in c["lines"]:
        if len(c["lines"]) > 1:
            d.ellipse([168, y + 16, 184, y + 32], fill=ACCENT)
            d.text((210, y), line, font=font(38), fill=(226, 232, 240))
        else:
            d.text((160, y), line, font=font(44), fill=(226, 232, 240))
        y += 64
    return canvas


def ssml(text: str) -> str:
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ru-RU">'
            f'<prosody rate="0.96">{esc}</prosody></speak>')


def powershell(script: str, *args: str) -> None:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / script), *args]
    subprocess.run(cmd, check=True)


def main():
    if not SKIP_CAPTURE:
        subprocess.run([sys.executable, str(HERE / "capture.py"), str(SHOTS)], check=True)
    meta = json.loads((SHOTS / "meta.json").read_text(encoding="utf-8"))
    total = len(SCENES)
    jobs = []
    for i, sc in enumerate(SCENES, 1):
        frame = render_card(sc["card"]) if "card" in sc else render_shot(sc["shot"], meta)
        chrome(frame, i, total, sc["chapter"], sc["title"], sc["sub"])
        fpath = FRAMES / f"{i:03d}.png"
        frame.save(fpath)
        spath = TTS / f"{i:03d}.ssml"
        old = spath.read_text(encoding="utf-8") if spath.exists() else None
        new = ssml(sc["say"])
        if old != new:
            spath.write_text(new, encoding="utf-8")
            (TTS / f"{i:03d}.wav").unlink(missing_ok=True)
        jobs.append((fpath, TTS / f"{i:03d}.wav"))
    print("Кадров:", total)
    powershell("tts.ps1", str(TTS))

    lead, tail = 0.35, 0.75
    rate = None
    clips, frames_all = [], []
    for fpath, wpath in jobs:
        with wave.open(str(wpath)) as w:
            rate = rate or w.getframerate()
            assert w.getframerate() == rate and w.getnchannels() == 1 and w.getsampwidth() == 2
            data = w.readframes(w.getnframes())
        pad1 = b"\x00\x00" * int(rate * lead)
        pad2 = b"\x00\x00" * int(rate * tail)
        chunk = pad1 + data + pad2
        frames_all.append(chunk)
        clips.append({"Image": str(fpath), "Seconds": round(len(chunk) / 2 / rate, 4)})
    audio = WORK / "narration.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames_all))
    (WORK / "clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
    total_s = sum(c["Seconds"] for c in clips)
    print(f"Длительность: {int(total_s // 60)} мин {int(total_s % 60)} с")
    OUT_MP4.parent.mkdir(parents=True, exist_ok=True)
    powershell("render.ps1", str(WORK / "clips.json"), str(audio), str(OUT_MP4))
    print("Видео:", OUT_MP4)


if __name__ == "__main__":
    main()
