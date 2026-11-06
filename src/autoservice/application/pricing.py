"""Расчёт строки продажи / заказ-наряда: себестоимость, наценка, цена, скидка."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.settings_service import default_markup
from autoservice.domain.errors import ValidationError
from autoservice.domain.markup import price_from_cost, resolve_markup
from autoservice.domain.money import ZERO, money, non_negative_money
from autoservice.persistence.models import Part


@dataclass
class LinePrice:
    cost_basis_total: Decimal | None
    cost_estimated: bool
    markup_pct: Decimal | None
    markup_source: str
    unit_price: Decimal
    amount_gross: Decimal
    enough_stock: bool
    available: Decimal


def price_line(
    s: Session,
    part: Part,
    qty: Decimal,
    markup_override=None,
    manual_unit_price=None,
    exclude_line_id: int | None = None,
) -> LinePrice:
    prev = inventory.preview_cost(s, part.id, qty, exclude_line_id=exclude_line_id)
    if prev.cost_total is None:
        # Себестоимость неизвестна — наценку применить не к чему, цену вводит менеджер.
        if manual_unit_price in (None, ""):
            raise ValidationError(
                f"Себестоимость «{part.name}» неизвестна (нет закупок с ценой). Укажите цену продажи вручную."
            )
        unit_price = non_negative_money(manual_unit_price, "Цена продажи")
        return LinePrice(None, False, None, "manual", unit_price, money(unit_price * qty), prev.enough, prev.available)
    if manual_unit_price not in (None, ""):
        raise ValidationError(
            "Себестоимость известна — цена рассчитывается по наценке 0–100 %. Выберите наценку вместо ручной цены."
        )
    res = resolve_markup(
        operation=None if markup_override in (None, "") else markup_override,
        batch=prev.first_batch_markup,
        part=part.markup_pct,
        default=default_markup(s),
    )
    if res.percent is None:
        raise ValidationError(
            "Наценка не задана: выберите наценку для строки (0–100 %) или задайте наценку запчасти / стандартную наценку в настройках."
        )
    unit_cost = prev.cost_total / qty
    unit_price = price_from_cost(unit_cost, res.percent)
    return LinePrice(prev.cost_total, prev.estimated, res.percent, res.source or "", unit_price,
                     money(unit_price * qty), prev.enough, prev.available)


def discount_for(amount_gross: Decimal, percent=None, amount=None) -> Decimal:
    if percent not in (None, "") and amount not in (None, ""):
        raise ValidationError("Укажите скидку либо в процентах, либо суммой")
    if percent not in (None, ""):
        from autoservice.domain.money import D

        p = D(percent)
        if p < 0 or p > 100:
            raise ValidationError("Скидка должна быть от 0 до 100 %")
        return money(amount_gross * p / Decimal(100))
    if amount not in (None, ""):
        d = non_negative_money(amount, "Скидка")
        if d > amount_gross:
            raise ValidationError("Скидка не может превышать сумму строки")
        return d
    return ZERO.quantize(Decimal("0.01"))
