"""Складской движок: партии, движения, резервы, FIFO.

Функции работают внутри транзакции вызывающей операции (принимают Session),
поэтому документ и складские движения сохраняются атомарно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoservice.application.context import AppContext
from autoservice.domain.errors import InsufficientStock, ValidationError
from autoservice.domain.fifo import Lot, allocate_fifo, total_cost
from autoservice.domain.money import ZERO, fmt_qty, money
from autoservice.persistence.models import Part, StockBatch, StockMovement, StockReservation, now


@dataclass
class CostPreview:
    qty: Decimal
    cost_total: Decimal | None  # себестоимость количества (FIFO + оценка недостающего)
    unit_cost: Decimal | None
    estimated: bool  # часть себестоимости оценена по последней закупке
    enough: bool
    available: Decimal
    first_batch_markup: Decimal | None


def fifo_batches(s: Session, part_id: int) -> list[StockBatch]:
    return list(
        s.execute(
            select(StockBatch)
            .where(StockBatch.part_id == part_id, StockBatch.qty_remaining > 0)
            .order_by(StockBatch.received_at, StockBatch.id)
        ).scalars()
    )


def _qty_value(value) -> Decimal:
    """Сумма по столбцу Qty: SQLAlchemy может вернуть уже Decimal или «сырое» целое."""
    if value is None:
        return Decimal("0.000")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.001"))
    return (Decimal(int(value)) / 1000).quantize(Decimal("0.001"))


def on_hand(s: Session, part_id: int) -> Decimal:
    return _qty_value(s.scalar(select(func.sum(StockBatch.qty_remaining)).where(StockBatch.part_id == part_id)))


def reserved(s: Session, part_id: int, exclude_line_id: int | None = None) -> Decimal:
    stmt = select(func.sum(StockReservation.qty)).where(
        StockReservation.part_id == part_id, StockReservation.status == "active"
    )
    if exclude_line_id is not None:
        stmt = stmt.where(StockReservation.work_order_part_id != exclude_line_id)
    return _qty_value(s.scalar(stmt))


def available(s: Session, part_id: int, exclude_line_id: int | None = None) -> Decimal:
    return on_hand(s, part_id) - reserved(s, part_id, exclude_line_id)


def last_known_unit_cost(s: Session, part_id: int) -> Decimal | None:
    batch = s.execute(
        select(StockBatch)
        .where(StockBatch.part_id == part_id, StockBatch.cost_total.is_not(None))
        .order_by(StockBatch.received_at.desc(), StockBatch.id.desc())
    ).scalars().first()
    if batch is None:
        return None
    return batch.cost_total / batch.qty_received


def preview_cost(s: Session, part_id: int, qty: Decimal, exclude_line_id: int | None = None) -> CostPreview:
    """Себестоимость будущего списания без изменения склада."""
    lots = [Lot(b.id, b.qty_remaining, b.cost_remaining, b.markup_pct) for b in fifo_batches(s, part_id)]
    first_markup = lots[0].markup if lots else None
    avail = available(s, part_id, exclude_line_id)
    stock_qty = sum((lot.qty_remaining for lot in lots), ZERO)
    take = min(qty, stock_qty)
    cost: Decimal | None = ZERO
    estimated = False
    if take > 0:
        cost = total_cost(allocate_fifo(lots, take))
    missing = qty - take
    if missing > 0 and cost is not None:
        last = last_known_unit_cost(s, part_id)
        if last is None:
            cost = None
        else:
            cost = money(cost + last * missing)
            estimated = True
    unit = (cost / qty) if (cost is not None and qty > 0) else None
    return CostPreview(qty, cost, unit, estimated, avail >= qty, avail, first_markup)


def receive(
    s: Session,
    ctx: AppContext,
    part_id: int,
    qty: Decimal,
    cost_total: Decimal | None,
    *,
    source_kind: str,
    movement_kind: str,
    doc_type: str,
    doc_id: int | None,
    line_id: int | None = None,
    source_ref: str = "",
    markup: Decimal | None = None,
    purchase_item_id: int | None = None,
    received_at: datetime | None = None,
    note: str = "",
) -> StockBatch:
    if qty <= 0:
        raise ValidationError("Количество поступления должно быть больше нуля")
    batch = StockBatch(
        part_id=part_id,
        purchase_item_id=purchase_item_id,
        source_kind=source_kind,
        source_ref=source_ref,
        received_at=received_at or now(),
        qty_received=qty,
        qty_remaining=qty,
        cost_total=cost_total,
        cost_remaining=cost_total,
        markup_pct=markup,
    )
    s.add(batch)
    s.flush()
    s.add(
        StockMovement(
            ts=now(),
            part_id=part_id,
            batch_id=batch.id,
            kind=movement_kind,
            qty=qty,
            cost=cost_total,
            doc_type=doc_type,
            doc_id=doc_id,
            line_id=line_id,
            user_id=ctx.user_id,
            note=note,
        )
    )
    return batch


def consume(
    s: Session,
    ctx: AppContext,
    part_id: int,
    qty: Decimal,
    *,
    movement_kind: str,
    doc_type: str,
    doc_id: int,
    line_id: int | None,
    own_reservation_line_id: int | None = None,
    note: str = "",
) -> tuple[Decimal | None, list[StockMovement]]:
    """Списывает по FIFO. Возвращает себестоимость (None — неизвестна) и движения."""
    if qty <= 0:
        raise ValidationError("Количество списания должно быть больше нуля")
    avail = available(s, part_id, exclude_line_id=own_reservation_line_id)
    if avail < qty:
        part = s.get(Part, part_id)
        name = part.name if part else f"№ {part_id}"
        raise InsufficientStock(
            f"Недостаточно товара «{name}»: требуется {fmt_qty(qty)}, доступно {fmt_qty(max(avail, ZERO))} "
            f"(с учётом резервов)"
        )
    batches = fifo_batches(s, part_id)
    by_id = {b.id: b for b in batches}
    portions = allocate_fifo([Lot(b.id, b.qty_remaining, b.cost_remaining) for b in batches], qty)
    movements = []
    for p in portions:
        b = by_id[p.batch_id]
        b.qty_remaining = b.qty_remaining - p.qty
        if b.cost_remaining is not None and p.cost is not None:
            b.cost_remaining = b.cost_remaining - p.cost
        m = StockMovement(
            ts=now(),
            part_id=part_id,
            batch_id=b.id,
            kind=movement_kind,
            qty=-p.qty,
            cost=(-p.cost if p.cost is not None else None),
            doc_type=doc_type,
            doc_id=doc_id,
            line_id=line_id,
            user_id=ctx.user_id,
            note=note,
        )
        s.add(m)
        movements.append(m)
    s.flush()
    return total_cost(portions), movements


def _reversed_totals(s: Session, movement_id: int) -> tuple[Decimal, Decimal | None, bool]:
    rows = s.execute(select(StockMovement).where(StockMovement.reversal_of_id == movement_id)).scalars().all()
    q = sum((r.qty for r in rows), ZERO)
    unknown = any(r.cost is None for r in rows)
    c = sum((r.cost for r in rows if r.cost is not None), ZERO)
    return q, c, unknown


def restore(
    s: Session,
    ctx: AppContext,
    original_movements: list[StockMovement],
    qty: Decimal,
    *,
    movement_kind: str,
    doc_type: str,
    doc_id: int,
    line_id: int | None = None,
    note: str = "",
) -> Decimal | None:
    """Возвращает товар в исходные партии (обратное движение к списанию).

    Порядок: от последней списанной партии к первой. Себестоимость возврата
    берётся из исходного списания, поэтому FIFO остаётся точным.
    """
    remaining = qty
    total: Decimal | None = ZERO
    for m in sorted(original_movements, key=lambda x: x.id, reverse=True):
        if remaining <= 0:
            break
        if m.qty >= 0:
            continue
        done_qty, done_cost, _ = _reversed_totals(s, m.id)
        restorable = -m.qty - done_qty
        if restorable <= 0:
            continue
        take = min(restorable, remaining)
        if m.cost is None:
            cost = None
        elif take == restorable:
            cost = -m.cost - (done_cost or ZERO)
        else:
            cost = money(-m.cost * take / -m.qty)
        batch = s.get(StockBatch, m.batch_id)
        batch.qty_remaining = batch.qty_remaining + take
        if batch.cost_remaining is not None and cost is not None:
            batch.cost_remaining = batch.cost_remaining + cost
        s.add(
            StockMovement(
                ts=now(),
                part_id=m.part_id,
                batch_id=m.batch_id,
                kind=movement_kind,
                qty=take,
                cost=cost,
                doc_type=doc_type,
                doc_id=doc_id,
                line_id=line_id,
                reversal_of_id=m.id,
                user_id=ctx.user_id,
                note=note,
            )
        )
        if total is not None:
            total = None if cost is None else total + cost
        remaining -= take
    if remaining > 0:
        raise ValidationError("Количество возврата больше, чем было списано")
    s.flush()
    return money(total) if total is not None else None


def remove_from_batch(
    s: Session,
    ctx: AppContext,
    batch: StockBatch,
    qty: Decimal,
    *,
    movement_kind: str,
    doc_type: str,
    doc_id: int,
    line_id: int | None = None,
    note: str = "",
) -> Decimal | None:
    """Списание из конкретной партии (возврат поставщику, отмена закупки)."""
    if qty <= 0 or qty > batch.qty_remaining:
        raise InsufficientStock(
            f"В партии осталось {fmt_qty(batch.qty_remaining)}, нельзя списать {fmt_qty(qty)}"
        )
    avail = available(s, batch.part_id)
    if avail < qty:
        raise InsufficientStock("Товар зарезервирован под заказ-наряды — сначала снимите резерв")
    if batch.cost_remaining is None:
        cost = None
    elif qty == batch.qty_remaining:
        cost = batch.cost_remaining
    else:
        cost = money(batch.cost_remaining * qty / batch.qty_remaining)
    batch.qty_remaining = batch.qty_remaining - qty
    if cost is not None:
        batch.cost_remaining = batch.cost_remaining - cost
    s.add(
        StockMovement(
            ts=now(),
            part_id=batch.part_id,
            batch_id=batch.id,
            kind=movement_kind,
            qty=-qty,
            cost=(-cost if cost is not None else None),
            doc_type=doc_type,
            doc_id=doc_id,
            line_id=line_id,
            user_id=ctx.user_id,
            note=note,
        )
    )
    s.flush()
    return cost


def movements_for(s: Session, doc_type: str, doc_id: int, line_id: int | None = None, kinds: set[str] | None = None):
    stmt = select(StockMovement).where(StockMovement.doc_type == doc_type, StockMovement.doc_id == doc_id)
    if line_id is not None:
        stmt = stmt.where(StockMovement.line_id == line_id)
    rows = list(s.execute(stmt.order_by(StockMovement.id)).scalars())
    if kinds:
        rows = [r for r in rows if r.kind in kinds]
    return rows


def stock_value(s: Session, part_id: int) -> tuple[Decimal, Decimal]:
    """(стоимость остатка с известной себестоимостью, количество без себестоимости)."""
    value = ZERO
    unknown_qty = ZERO
    for b in fifo_batches(s, part_id):
        if b.cost_remaining is None:
            unknown_qty += b.qty_remaining
        else:
            value += b.cost_remaining
    return money(value), unknown_qty


def stock_summary(s: Session) -> dict[int, dict]:
    """Остатки по всем запчастям одним запросом."""
    result: dict[int, dict] = {}
    rows = s.execute(
        select(StockBatch.part_id, StockBatch.qty_remaining, StockBatch.cost_remaining).where(StockBatch.qty_remaining > 0)
    ).all()
    for part_id, q, c in rows:
        r = result.setdefault(part_id, {"on_hand": ZERO, "value": ZERO, "unknown_qty": ZERO, "reserved": ZERO})
        r["on_hand"] += q
        if c is None:
            r["unknown_qty"] += q
        else:
            r["value"] += c
    for part_id, q in s.execute(
        select(StockReservation.part_id, StockReservation.qty).where(StockReservation.status == "active")
    ).all():
        r = result.setdefault(part_id, {"on_hand": ZERO, "value": ZERO, "unknown_qty": ZERO, "reserved": ZERO})
        r["reserved"] += q
    for r in result.values():
        r["available"] = r["on_hand"] - r["reserved"]
    return result
