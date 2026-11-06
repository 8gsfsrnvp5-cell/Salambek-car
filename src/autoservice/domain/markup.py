"""Наценка 0–100 % от себестоимости.

Формула: Цена = Себестоимость × (1 + Наценка / 100), цена за единицу округляется
до копейки (ROUND_HALF_UP).

Приоритет правил (от высшего к низшему):
1. наценка, указанная в строке конкретной операции (продажа / заказ-наряд);
2. наценка закупочной партии, из которой будет списан товар (первая партия FIFO);
3. индивидуальная наценка запчасти;
4. стандартная наценка по умолчанию из настроек.
Если ни одно правило не задано, цена не рассчитывается — менеджер выбирает
наценку явно.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autoservice.domain.errors import ValidationError
from autoservice.domain.money import D, PCT_STEP, money

MARKUP_MIN = Decimal("0")
MARKUP_MAX = Decimal("100")
MARKUP_PRESETS = [Decimal(p) for p in range(0, 101, 10)]

SOURCE_OPERATION = "operation"
SOURCE_BATCH = "batch"
SOURCE_PART = "part"
SOURCE_DEFAULT = "default"

SOURCE_TITLES = {
    SOURCE_OPERATION: "указана в операции",
    SOURCE_BATCH: "наценка партии",
    SOURCE_PART: "наценка запчасти",
    SOURCE_DEFAULT: "стандартная",
}


def validate_markup(value) -> Decimal:
    p = D(value)
    if p != p.quantize(PCT_STEP):
        raise ValidationError("Наценка: не более двух знаков после запятой")
    p = p.quantize(PCT_STEP)
    if p < MARKUP_MIN or p > MARKUP_MAX:
        raise ValidationError("Наценка должна быть в диапазоне от 0 до 100 %")
    return p


def price_from_cost(unit_cost, markup) -> Decimal:
    """Цена за единицу по себестоимости единицы и наценке."""
    cost = D(unit_cost)
    if cost < 0:
        raise ValidationError("Себестоимость не может быть отрицательной")
    m = validate_markup(markup)
    return money(cost * (Decimal(1) + m / Decimal(100)))


@dataclass(frozen=True)
class MarkupResolution:
    percent: Decimal | None
    source: str | None

    @property
    def title(self) -> str:
        return SOURCE_TITLES.get(self.source or "", "не задана")


def resolve_markup(operation=None, batch=None, part=None, default=None) -> MarkupResolution:
    for value, source in (
        (operation, SOURCE_OPERATION),
        (batch, SOURCE_BATCH),
        (part, SOURCE_PART),
        (default, SOURCE_DEFAULT),
    ):
        if value is not None:
            return MarkupResolution(validate_markup(value), source)
    return MarkupResolution(None, None)
