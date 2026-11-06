"""Складские документы: начальные остатки, списание, оприходование, инвентаризация."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from autoservice.application import inventory
from autoservice.application.context import AppContext, as_date, audit, get_or_404, next_number
from autoservice.domain.enums import MovementKind
from autoservice.domain.errors import ValidationError
from autoservice.domain.money import ZERO, money, non_negative_money, positive_qty, qty as to_qty
from autoservice.persistence.models import (
    InventoryCount,
    InventoryCountItem,
    Part,
    StockAdjustment,
    StockMovement,
    now,
)
from autoservice.security.permissions import Perm


def _adjust_in(ctx: AppContext, part_id: int, qty, unit_cost, reason: str, kind: str, doc_date=None) -> int:
    ctx.require(Perm.STOCK_OPS)
    q = positive_qty(qty)
    if not (reason or "").strip():
        raise ValidationError("Укажите основание операции")
    unit = None if unit_cost in (None, "") else non_negative_money(unit_cost, "Себестоимость единицы")
    with ctx.db.transaction() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        cost = money(unit * q) if unit is not None else None
        adj = StockAdjustment(number=next_number(s, "adjustment", "КР-", 6), doc_date=as_date(doc_date), kind=kind,
                              reason=reason.strip(), part_id=part.id, qty=q, unit_cost=unit, cost=cost, created_by=ctx.user_id)
        s.add(adj)
        s.flush()
        inventory.receive(s, ctx, part.id, q, cost, source_kind=kind,
                          movement_kind=MovementKind.OPENING_IN if kind == "opening" else MovementKind.ADJUST_IN,
                          doc_type="adjustment", doc_id=adj.id, source_ref=adj.number, note=reason)
        audit(s, ctx, f"stock.{kind}", "part", part.id, number=adj.number, qty=str(q), cost=str(cost) if cost is not None else None)
        return adj.id


def enter_opening_balance(ctx: AppContext, part_id: int, qty, unit_cost=None, reason: str = "Ввод начальных остатков", doc_date=None) -> int:
    """Начальный остаток при начале работы с программой. Без себестоимости прибыль по нему не считается."""
    return _adjust_in(ctx, part_id, qty, unit_cost, reason, "opening", doc_date)


def register_surplus(ctx: AppContext, part_id: int, qty, unit_cost=None, reason: str = "", doc_date=None) -> int:
    return _adjust_in(ctx, part_id, qty, unit_cost, reason, "surplus", doc_date)


def write_off(ctx: AppContext, part_id: int, qty, reason: str, doc_date=None) -> int:
    """Списание (брак, порча, утеря) по FIFO."""
    ctx.require(Perm.STOCK_OPS)
    q = positive_qty(qty)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину списания")
    with ctx.db.transaction() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        adj = StockAdjustment(number=next_number(s, "adjustment", "КР-", 6), doc_date=as_date(doc_date), kind="write_off",
                              reason=reason.strip(), part_id=part.id, qty=q, created_by=ctx.user_id)
        s.add(adj)
        s.flush()
        cost, _ = inventory.consume(s, ctx, part.id, q, movement_kind=MovementKind.ADJUST_OUT, doc_type="adjustment",
                                    doc_id=adj.id, line_id=None, note=reason)
        adj.cost = cost
        audit(s, ctx, "stock.write_off", "part", part.id, number=adj.number, qty=str(q), reason=reason)
        return adj.id


# --------------------------------------------------------------------------- инвентаризация


def create_inventory(ctx: AppContext, part_ids: list[int] | None = None, category_id: int | None = None, notes: str = "") -> int:
    ctx.require(Perm.STOCK_OPS)
    with ctx.db.transaction() as s:
        stmt = select(Part).where(Part.is_active.is_(True))
        if part_ids:
            stmt = stmt.where(Part.id.in_(part_ids))
        if category_id:
            stmt = stmt.where(Part.category_id == category_id)
        parts = s.execute(stmt.order_by(Part.name)).scalars().all()
        if not parts:
            raise ValidationError("Нет запчастей для инвентаризации")
        count = InventoryCount(number=next_number(s, "inventory", "ИН-", 6), doc_date=as_date(None), notes=notes, created_by=ctx.user_id)
        for p in parts:
            count.items.append(InventoryCountItem(part_id=p.id, qty_expected=inventory.on_hand(s, p.id)))
        s.add(count)
        s.flush()
        audit(s, ctx, "inventory.create", "inventory", count.id, number=count.number, items=len(parts))
        return count.id


def set_actual(ctx: AppContext, item_id: int, qty_actual, unit_cost_for_surplus=None) -> None:
    ctx.require(Perm.STOCK_OPS)
    with ctx.db.transaction() as s:
        item = get_or_404(s, InventoryCountItem, item_id, "Строка инвентаризации")
        count = s.get(InventoryCount, item.count_id)
        if count.status != "draft":
            raise ValidationError("Инвентаризация уже проведена")
        item.qty_actual = None if qty_actual in (None, "") else to_qty(qty_actual)
        if item.qty_actual is not None and item.qty_actual < 0:
            raise ValidationError("Фактическое количество не может быть отрицательным")
        item.unit_cost_for_surplus = None if unit_cost_for_surplus in (None, "") else non_negative_money(unit_cost_for_surplus, "Себестоимость")


def post_inventory(ctx: AppContext, count_id: int) -> dict:
    """Проведение: недостача списывается по FIFO, излишек приходуется (себестоимость — если указана)."""
    ctx.require(Perm.STOCK_OPS)
    result = {"shortage_lines": 0, "surplus_lines": 0, "shortage_cost": ZERO, "unknown_cost_lines": 0}
    with ctx.db.transaction() as s:
        count = get_or_404(s, InventoryCount, count_id, "Инвентаризация")
        if count.status != "draft":
            raise ValidationError("Инвентаризация уже проведена")
        missing = [i for i in count.items if i.qty_actual is None]
        if missing:
            raise ValidationError(f"Не указан фактический остаток в {len(missing)} строках")
        for item in count.items:
            item.qty_expected = inventory.on_hand(s, item.part_id)
            diff = item.qty_actual - item.qty_expected
            item.qty_diff = diff
            if diff < 0:
                reserved_qty = inventory.reserved(s, item.part_id)
                if item.qty_actual < reserved_qty:
                    raise ValidationError(
                        f"Фактический остаток «{s.get(Part, item.part_id).name}» меньше резерва под заказ-наряды — сначала снимите резерв"
                    )
                # Факт не меньше резерва, значит недостача не затрагивает зарезервированное.
                cost, _ = inventory.consume(s, ctx, item.part_id, -diff, movement_kind=MovementKind.INVENTORY_OUT,
                                            doc_type="inventory", doc_id=count.id, line_id=item.id,
                                            note="Недостача по инвентаризации")
                item.cost_diff = -cost if cost is not None else None
                result["shortage_lines"] += 1
                if cost is None:
                    result["unknown_cost_lines"] += 1
                else:
                    result["shortage_cost"] += cost
            elif diff > 0:
                cost = money(item.unit_cost_for_surplus * diff) if item.unit_cost_for_surplus is not None else None
                inventory.receive(s, ctx, item.part_id, diff, cost, source_kind="inventory_surplus",
                                  movement_kind=MovementKind.INVENTORY_IN, doc_type="inventory", doc_id=count.id,
                                  line_id=item.id, source_ref=count.number, note="Излишек по инвентаризации")
                item.cost_diff = cost
                result["surplus_lines"] += 1
            else:
                item.cost_diff = ZERO
        count.status = "posted"
        count.posted_at = now()
        count.posted_by = ctx.user_id
        audit(s, ctx, "inventory.post", "inventory", count.id, number=count.number,
              shortage_lines=result["shortage_lines"], surplus_lines=result["surplus_lines"])
    result["shortage_cost"] = money(result["shortage_cost"])
    return result


def get_inventory(ctx: AppContext, count_id: int) -> dict:
    with ctx.db.read() as s:
        c = get_or_404(s, InventoryCount, count_id, "Инвентаризация")
        items = []
        for i in c.items:
            p = s.get(Part, i.part_id)
            items.append({"id": i.id, "part_id": p.id, "part_code": p.code, "part_name": p.name, "oem_number": p.oem_number,
                          "unit": p.unit, "location": p.location,
                          "qty_expected": i.qty_expected if c.status == "posted" else inventory.on_hand(s, p.id),
                          "qty_actual": i.qty_actual, "qty_diff": i.qty_diff, "unit_cost_for_surplus": i.unit_cost_for_surplus,
                          "cost_diff": i.cost_diff})
        return {"id": c.id, "number": c.number, "doc_date": c.doc_date, "status": c.status,
                "status_title": {"draft": "Черновик", "posted": "Проведена", "cancelled": "Отменена"}[c.status],
                "notes": c.notes, "items": items}


def list_inventories(ctx: AppContext) -> list[dict]:
    with ctx.db.read() as s:
        return [
            {"id": c.id, "number": c.number, "doc_date": c.doc_date, "status": c.status,
             "status_title": {"draft": "Черновик", "posted": "Проведена", "cancelled": "Отменена"}[c.status],
             "items": len(c.items), "notes": c.notes}
            for c in s.execute(select(InventoryCount).order_by(InventoryCount.id.desc())).scalars()
        ]


def list_adjustments(ctx: AppContext, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    titles = {"write_off": "Списание", "surplus": "Оприходование", "opening": "Начальный остаток"}
    with ctx.db.read() as s:
        stmt = select(StockAdjustment).order_by(StockAdjustment.id.desc())
        if date_from:
            stmt = stmt.where(StockAdjustment.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(StockAdjustment.doc_date <= date_to)
        out = []
        for a in s.execute(stmt).scalars():
            p = s.get(Part, a.part_id)
            out.append({"id": a.id, "number": a.number, "doc_date": a.doc_date, "kind": a.kind, "kind_title": titles[a.kind],
                        "part_name": p.name, "part_code": p.code, "qty": a.qty, "cost": a.cost, "reason": a.reason})
        return out


def part_movements(ctx: AppContext, part_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                   limit: int = 2000) -> list[dict]:
    from sqlalchemy import func

    from autoservice.persistence.models import User

    with ctx.db.read() as s:
        stmt = select(StockMovement).order_by(StockMovement.ts.desc(), StockMovement.id.desc()).limit(limit)
        if part_id:
            stmt = stmt.where(StockMovement.part_id == part_id)
        if date_from:
            stmt = stmt.where(func.date(StockMovement.ts) >= date_from.isoformat())
        if date_to:
            stmt = stmt.where(func.date(StockMovement.ts) <= date_to.isoformat())
        users = dict(s.execute(select(User.id, User.username)).all())
        out = []
        for m in s.execute(stmt).scalars():
            p = s.get(Part, m.part_id)
            out.append({"id": m.id, "ts": m.ts, "part_id": p.id, "part_code": p.code, "part_name": p.name, "kind": m.kind,
                        "kind_title": MovementKind.TITLES.get(m.kind, m.kind), "qty": m.qty, "cost": m.cost,
                        "doc_type": m.doc_type, "doc_id": m.doc_id, "batch_id": m.batch_id, "user": users.get(m.user_id, ""),
                        "note": m.note})
        return out


def batches_of(ctx: AppContext, part_id: int) -> list[dict]:
    from autoservice.persistence.models import StockBatch

    with ctx.db.read() as s:
        return [
            {"id": b.id, "received_at": b.received_at, "source_kind": b.source_kind, "source_ref": b.source_ref,
             "qty_received": b.qty_received, "qty_remaining": b.qty_remaining, "cost_total": b.cost_total,
             "cost_remaining": b.cost_remaining,
             "unit_cost": (b.cost_total / b.qty_received).quantize(Decimal("0.01")) if b.cost_total is not None else None,
             "markup_pct": b.markup_pct}
            for b in s.execute(select(StockBatch).where(StockBatch.part_id == part_id).order_by(StockBatch.received_at, StockBatch.id)).scalars()
        ]


def set_batch_markup(ctx: AppContext, batch_id: int, markup_pct) -> None:
    """Наценка закупочной партии (приоритет выше наценки запчасти)."""
    from autoservice.domain.markup import validate_markup
    from autoservice.persistence.models import PriceHistory, StockBatch

    ctx.require(Perm.CATALOG_EDIT)
    with ctx.db.transaction() as s:
        b = get_or_404(s, StockBatch, batch_id, "Партия")
        b.markup_pct = None if markup_pct in (None, "") else validate_markup(markup_pct)
        s.add(PriceHistory(part_id=b.part_id, kind="markup", percent=b.markup_pct, source_doc=f"партия {b.source_ref}",
                           user_id=ctx.user_id))
        audit(s, ctx, "stock.batch_markup", "part", b.part_id, batch_id=b.id, markup=str(b.markup_pct))
