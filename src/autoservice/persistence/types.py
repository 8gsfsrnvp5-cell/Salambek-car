"""Типы столбцов: деньги, количество и проценты хранятся целыми числами."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import BigInteger, Text
from sqlalchemy.types import TypeDecorator

from autoservice.domain.money import CENT, D, PCT_STEP, QTY_STEP


class _ScaledDecimal(TypeDecorator):
    impl = BigInteger
    cache_ok = True
    scale: int = 100
    step: Decimal = CENT
    label = "значение"

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = D(value)
        q = d.quantize(self.step, rounding=ROUND_HALF_UP)
        if q != d:
            raise ValueError(f"{self.label}: лишние знаки после запятой ({value}) — округлите явно")
        return int(q * self.scale)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(int(value)) / self.scale).quantize(self.step)


class Money(_ScaledDecimal):
    """Сумма в рублях; в базе — целые копейки."""

    cache_ok = True
    scale = 100
    step = CENT
    label = "Сумма"


class Qty(_ScaledDecimal):
    """Количество; в базе — тысячные доли."""

    cache_ok = True
    scale = 1000
    step = QTY_STEP
    label = "Количество"


class Percent(_ScaledDecimal):
    """Процент; в базе — сотые доли процента."""

    cache_ok = True
    scale = 100
    step = PCT_STEP
    label = "Процент"


class JSONText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)
