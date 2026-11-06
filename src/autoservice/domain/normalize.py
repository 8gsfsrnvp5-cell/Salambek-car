"""Нормализация артикулов, госномеров и VIN для поиска."""

from __future__ import annotations

import re

# Кириллические буквы, внешне совпадающие с латинскими (частая ошибка ввода и OCR).
_LOOKALIKE = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y",
    "а": "A", "в": "B", "е": "E", "к": "K", "м": "M", "н": "H", "о": "O",
    "р": "P", "с": "C", "т": "T", "х": "X", "у": "Y",
})

_ARTICLE_STRIP = re.compile(r"[\s\-./_\\]+")
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize_article(value: str | None) -> str:
    if not value:
        return ""
    return _ARTICLE_STRIP.sub("", value.translate(_LOOKALIKE)).upper()


def normalize_plate(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\s\-]+", "", value.translate(_LOOKALIKE)).upper()


def normalize_vin(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.translate(_LOOKALIKE)).upper()


def vin_warning(vin: str | None) -> str | None:
    v = normalize_vin(vin)
    if not v:
        return "VIN не указан — карточка автомобиля неполная"
    if len(v) == 17 and not VIN_RE.match(v):
        return "VIN содержит недопустимые символы (I, O, Q не используются в VIN)"
    if len(v) != 17:
        return "VIN нестандартной длины (не 17 символов) — проверьте, возможно это номер кузова"
    return None


def normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def normalize_text(value: str | None) -> str:
    return (value or "").strip().casefold().replace("ё", "е")
