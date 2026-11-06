"""Безопасная работа с путями при импорте и хранении файлов."""

from __future__ import annotations

import re
from pathlib import Path

from autoservice.domain.errors import ValidationError

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

# Файлы, которые программа принимает для хранения/распознавания. Исполняемые
# форматы не принимаются и никогда не запускаются.
ALLOWED_IMPORT_EXT = {
    ".pdf", ".txt", ".docx", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".json",
}
MAX_IMPORT_BYTES = 200 * 1024 * 1024


def safe_filename(name: str, fallback: str = "file") -> str:
    base = Path(name).name
    base = _FORBIDDEN.sub("_", base).strip(" .")
    if not base:
        base = fallback
    stem = base.split(".")[0].upper()
    if stem in _RESERVED:
        base = f"_{base}"
    return base[:150]


def ensure_inside(base: Path, candidate: Path) -> Path:
    base_r = base.resolve()
    cand_r = candidate.resolve()
    if cand_r != base_r and base_r not in cand_r.parents:
        raise ValidationError("Недопустимый путь к файлу")
    return cand_r


def check_import_file(path: Path, allowed: set[str] | None = None) -> Path:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Файл не найден: {path}")
    ext = path.suffix.lower()
    allowed = allowed or ALLOWED_IMPORT_EXT
    if ext not in allowed:
        raise ValidationError(f"Формат «{ext or 'без расширения'}» не поддерживается")
    size = path.stat().st_size
    if size == 0:
        raise ValidationError("Файл пустой")
    if size > MAX_IMPORT_BYTES:
        raise ValidationError("Файл слишком большой (более 200 МБ)")
    return path
