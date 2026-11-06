"""Извлечение текста из документов (PDF, DOCX, TXT, XLSX, CSV, изображения).

Импортируемые файлы только читаются: макросы, скрипты и встроенные объекты
не выполняются (DOCX/XLSX читаются как XML-данные, PDF — как текст).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from autoservice.ai import ocr

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass
class ExtractResult:
    text: str
    engine: str
    warnings: list[str] = field(default_factory=list)


def decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def docx_text(path: Path) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)  # noqa: S314 — локальный файл пользователя, без внешних сущностей
    paragraphs = []
    for p in root.iter(f"{ns}p"):
        texts = [t.text or "" for t in p.iter(f"{ns}t")]
        paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def table_rows(path: Path) -> list[list[str]]:
    ext = path.suffix.lower()
    if ext == ".csv":
        text = decode_text(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        return [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = []
        for ws in wb.worksheets[:3]:
            for row in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v) for v in row])
        return rows
    finally:
        wb.close()


def pdf_text(path: Path) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages), len(pages)


def extract_text(path: Path, ocr_status: ocr.OcrStatus | None = None, languages: str = "rus+eng") -> ExtractResult:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".txt":
        return ExtractResult(decode_text(path.read_bytes()), "text")
    if ext == ".docx":
        return ExtractResult(docx_text(path), "docx")
    if ext in (".xlsx", ".csv"):
        return ExtractResult("\n".join("\t".join(r) for r in table_rows(path)), ext[1:])
    if ext == ".pdf":
        text, pages = pdf_text(path)
        if len(re.sub(r"\s+", "", text)) >= 20 * max(pages, 1) * 0.5:
            return ExtractResult(text, "pdf_text")
        if ocr_status and ocr_status.available:
            return ExtractResult(ocr.recognize_pdf_scan(path, ocr_status, languages), "ocr_tesseract",
                                 ["Текст получен распознаванием скана — проверьте значения"])
        return ExtractResult(text, "pdf_text", ["PDF не содержит текстового слоя (скан). " + (ocr_status.text if ocr_status else "OCR недоступен")])
    if ext in IMAGE_EXT:
        if ocr_status and ocr_status.available:
            return ExtractResult(ocr.recognize_image(path, ocr_status, languages), "ocr_tesseract",
                                 ["Текст получен распознаванием изображения — проверьте значения"])
        return ExtractResult("", "none", [ocr_status.text if ocr_status else "OCR недоступен"])
    return ExtractResult("", "none", [f"Формат {ext} не поддерживается для извлечения текста"])
