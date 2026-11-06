"""Локальное распознавание текста (OCR). Никаких облачных сервисов.

Поддерживается движок Tesseract OCR (Apache 2.0), установленный на компьютере
или поставленный вместе с программой в папке ``ocr``. Если движка нет,
программа честно сообщает об этом: PDF с текстовым слоем и таблицы Excel
распознаются без OCR, а сканы можно ввести вручную.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from autoservice.config.paths import executable_dir
from autoservice.domain.errors import DomainError

CREATE_NO_WINDOW = 0x08000000


@dataclass
class OcrStatus:
    available: bool
    engine: str
    text: str
    path: str = ""
    languages: tuple[str, ...] = ()


def _candidates(configured: str = "") -> list[Path]:
    paths = []
    if configured:
        paths.append(Path(configured))
    paths.append(executable_dir() / "ocr" / "tesseract.exe")
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            paths.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
            paths.append(Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    found = shutil.which("tesseract")
    if found:
        paths.append(Path(found))
    return paths


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    kwargs = {"capture_output": True, "timeout": timeout}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)  # noqa: S603 — запускается только найденный tesseract.exe


def tesseract_status(configured: str = "", languages: str = "rus+eng") -> OcrStatus:
    for exe in _candidates(configured):
        if exe.is_file():
            try:
                out = _run([str(exe), "--list-langs"], timeout=15)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return OcrStatus(False, "tesseract", f"Tesseract найден, но не запускается: {exc}", str(exe))
            langs = tuple(line.strip() for line in out.stdout.decode("utf-8", "replace").splitlines()[1:] if line.strip())
            need = [lang for lang in languages.split("+") if lang]
            missing = [lang for lang in need if lang not in langs]
            if missing:
                return OcrStatus(False, "tesseract", f"Tesseract найден, но нет языков: {', '.join(missing)}", str(exe), langs)
            return OcrStatus(True, "tesseract", f"Tesseract OCR ({', '.join(need)})", str(exe), langs)
    return OcrStatus(False, "none", "OCR-движок не установлен: распознавание сканов и фото недоступно. "
                                   "PDF с текстом и Excel-накладные распознаются без OCR.")


def recognize_image(image: Path, status: OcrStatus, languages: str = "rus+eng") -> str:
    if not status.available:
        raise DomainError(status.text)
    out = _run([status.path, str(image), "stdout", "-l", languages, "--psm", "6"], timeout=180)
    if out.returncode != 0:
        raise DomainError(f"Ошибка распознавания: {out.stderr.decode('utf-8', 'replace')[:300]}")
    return out.stdout.decode("utf-8", "replace")


def pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = 300, max_pages: int = 20) -> list[Path]:
    """Растеризация страниц PDF средствами QtPdf (без внешних программ)."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument

    if QGuiApplication.instance() is None:
        raise DomainError("Растеризация PDF требует запущенного интерфейса")
    doc = QPdfDocument()
    if doc.load(str(pdf_path)) != QPdfDocument.Error.None_:
        raise DomainError("Не удалось открыть PDF")
    images = []
    try:
        for i in range(min(doc.pageCount(), max_pages)):
            size = doc.pagePointSize(i)
            img = doc.render(i, QSize(int(size.width() / 72 * dpi), int(size.height() / 72 * dpi)))
            target = Path(out_dir) / f"page-{i + 1}.png"
            img.save(str(target))
            images.append(target)
    finally:
        doc.close()
    return images


def recognize_pdf_scan(pdf_path: Path, status: OcrStatus, languages: str = "rus+eng") -> str:
    with tempfile.TemporaryDirectory() as tmp:
        pages = pdf_to_images(pdf_path, Path(tmp))
        return "\n".join(recognize_image(p, status, languages) for p in pages)
