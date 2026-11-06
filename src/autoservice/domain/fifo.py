"""Списание себестоимости по FIFO.

Партия хранит остаток количества и остаток стоимости (в копейках). При
частичном списании стоимость берётся пропорционально, при списании остатка
партии — весь остаток стоимости. Так сумма списаний по партии всегда равна её
стоимости, копейки не теряются.

Партия с неизвестной себестоимостью (``cost_remaining is None``) даёт
неизвестную себестоимость списания — прибыль по ней не показывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autoservice.domain.errors import InsufficientStock
from autoservice.domain.money import ZERO, fmt_qty, money


@dataclass(frozen=True)
class Lot:
    batch_id: int
    qty_remaining: Decimal
    cost_remaining: Decimal | None
    markup: Decimal | None = None


@dataclass(frozen=True)
class Portion:
    batch_id: int
    qty: Decimal
    cost: Decimal | None


def allocate_fifo(lots: list[Lot], qty: Decimal) -> list[Portion]:
    """Подбирает партии для списания ``qty``. ``lots`` уже упорядочены по FIFO."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    remaining = qty
    portions: list[Portion] = []
    for lot in lots:
        if remaining <= 0:
            break
        if lot.qty_remaining <= 0:
            continue
        take = min(lot.qty_remaining, remaining)
        if lot.cost_remaining is None:
            cost = None
        elif take == lot.qty_remaining:
            cost = lot.cost_remaining
        else:
            cost = money(lot.cost_remaining * take / lot.qty_remaining)
        portions.append(Portion(lot.batch_id, take, cost))
        remaining -= take
    if remaining > 0:
        available = qty - remaining
        raise InsufficientStock(
            f"Недостаточно остатка: требуется {fmt_qty(qty)}, доступно {fmt_qty(available)}"
        )
    return portions


def total_cost(portions: list[Portion]) -> Decimal | None:
    """Себестоимость списания или None, если хотя бы одна часть без себестоимости."""
    total = ZERO
    for p in portions:
        if p.cost is None:
            return None
        total += p.cost
    return money(total)
