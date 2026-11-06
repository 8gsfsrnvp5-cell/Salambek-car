"""Рекомендации по закупке. Каждая рекомендация содержит основание расчёта.

Закупка не создаётся автоматически: менеджер выбирает строки, программа
готовит черновики, проведение — отдельное подтверждение.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from autoservice.application import inventory
from autoservice.application.context import AppContext, audit
from autoservice.domain.enums import DocStatus, MovementKind, WOStatus
from autoservice.domain.money import ZERO, fmt_qty, money
from autoservice.persistence.models import (
    AIRecommendation,
    Part,
    PartSupplier,
    PurchaseItem,
    PurchaseOrder,
    StockMovement,
    StockReservation,
    Supplier,
    WorkOrder,
    WorkOrderPart,
)
from autoservice.security.permissions import Perm

WINDOW_DAYS = 90
MIN_HISTORY_DAYS = 30
MIN_EVENTS = 3

OUT_KINDS = {MovementKind.SALE_OUT, MovementKind.WO_ISSUE}
BACK_KINDS = {MovementKind.SALE_RETURN_IN, MovementKind.WO_RETURN_IN, MovementKind.SALE_CANCEL_IN}


def _round_up(q: Decimal, unit: str) -> Decimal:
    if unit in ("л", "кг", "м"):
        return Decimal(math.ceil(q * 10)) / 10
    return Decimal(math.ceil(q))


def purchase_recommendations(ctx: AppContext, window_days: int = WINDOW_DAYS) -> list[dict]:
    now = datetime.now()
    since = now - timedelta(days=window_days)
    with ctx.db.read() as s:
        summary = inventory.stock_summary(s)
        consumption: dict[int, Decimal] = defaultdict(lambda: ZERO)
        events: dict[int, set] = defaultdict(set)
        for m in s.execute(select(StockMovement).where(StockMovement.ts >= since, StockMovement.kind.in_(OUT_KINDS | BACK_KINDS))).scalars():
            if m.kind in OUT_KINDS:
                consumption[m.part_id] += -m.qty
                events[m.part_id].add((m.doc_type, m.doc_id))
            else:
                consumption[m.part_id] -= m.qty
        first_seen = dict(
            s.execute(select(StockMovement.part_id, StockMovement.ts).order_by(StockMovement.ts.desc())).all()
        )  # последний перезаписывает → самая ранняя дата
        # потребность под открытые заказ-наряды, не покрытая резервом
        shortage: dict[int, Decimal] = defaultdict(lambda: ZERO)
        for line, wo in s.execute(
            select(WorkOrderPart, WorkOrder).join(WorkOrder, WorkOrder.id == WorkOrderPart.work_order_id)
            .where(WorkOrder.status.in_(WOStatus.OPEN), WorkOrderPart.qty > WorkOrderPart.issued_qty)
        ).all():
            reserved = sum(
                (r.qty for r in s.execute(select(StockReservation).where(
                    StockReservation.work_order_part_id == line.id, StockReservation.status == "active")).scalars()),
                ZERO,
            )
            gap = line.qty - line.issued_qty - reserved
            if gap > 0:
                shortage[line.part_id] += gap

        result = []
        for part in s.execute(select(Part).where(Part.is_active.is_(True))).scalars():
            st = summary.get(part.id, {"available": ZERO, "on_hand": ZERO, "reserved": ZERO})
            avail = st["available"]
            used = max(consumption.get(part.id, ZERO), ZERO)
            n_events = len(events.get(part.id, ()))
            started = first_seen.get(part.id)
            history_days = min(window_days, max((now - started).days, 0)) if started else 0
            wo_gap = shortage.get(part.id, ZERO)
            if part.min_stock <= 0 and wo_gap <= 0 and used <= 0:
                continue

            last = s.execute(
                select(PurchaseItem, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == PurchaseItem.purchase_id)
                .where(PurchaseItem.part_id == part.id, PurchaseOrder.status == DocStatus.POSTED)
                .order_by(PurchaseOrder.doc_date.desc(), PurchaseOrder.id.desc())
            ).first()
            supplier_id = last[1].supplier_id if last else None
            link = None
            if supplier_id:
                link = s.execute(select(PartSupplier).where(PartSupplier.part_id == part.id, PartSupplier.supplier_id == supplier_id)).scalars().first()
            if link is None:
                link = s.execute(select(PartSupplier).where(PartSupplier.part_id == part.id)).scalars().first()
                supplier_id = supplier_id or (link.supplier_id if link else None)
            supplier = s.get(Supplier, supplier_id) if supplier_id else None
            lead = (link.lead_time_days if link and link.lead_time_days is not None else None)
            if lead is None and supplier is not None:
                lead = supplier.lead_time_days

            reliable = history_days >= MIN_HISTORY_DAYS and n_events >= MIN_EVENTS and lead is not None
            basis = [f"Доступно {fmt_qty(avail)} {part.unit} (остаток {fmt_qty(st['on_hand'])}, резерв {fmt_qty(st['reserved'])})"]
            target = part.min_stock
            if part.min_stock > 0:
                basis.append(f"минимальный остаток {fmt_qty(part.min_stock)}")
            lead_demand = ZERO
            if used > 0 and history_days > 0:
                daily = used / Decimal(history_days)
                basis.append(f"расход за {history_days} дн.: {fmt_qty(used)} ({n_events} док.)")
                if lead is not None:
                    lead_demand = daily * Decimal(lead)
                    basis.append(f"срок поставки {lead} дн. → потребность {fmt_qty(lead_demand.quantize(Decimal('0.001')))}")
                else:
                    basis.append("срок поставки не указан — потребность на время поставки не рассчитана")
            if wo_gap > 0:
                basis.append(f"не хватает под открытые заказ-наряды: {fmt_qty(wo_gap)}")
            need = target + lead_demand + wo_gap - avail
            if need <= 0:
                continue
            qty_rec = _round_up(need, part.unit)
            result.append({
                "part_id": part.id, "part_code": part.code, "part_name": part.name, "article": part.oem_number, "unit": part.unit,
                "available": avail, "min_stock": part.min_stock, "consumption": used, "history_days": history_days,
                "events": n_events, "wo_shortage": wo_gap, "lead_time_days": lead, "recommended_qty": qty_rec,
                "supplier_id": supplier.id if supplier else None, "supplier": supplier.name if supplier else "",
                "last_price": last[0].unit_price if last else None, "last_price_date": last[1].doc_date if last else None,
                "reliability": "Расчёт по истории" if reliable else "Недостаточно данных для прогноза",
                "reliable": reliable, "basis": "; ".join(basis),
            })
        result.sort(key=lambda r: (r["wo_shortage"] <= 0, r["available"] - r["min_stock"]))
        return result


def create_purchase_drafts(ctx: AppContext, items: list[dict]) -> list[int]:
    """Черновики закупок по выбранным рекомендациям (по поставщикам). Ничего не проводит."""
    from autoservice.application import purchasing

    ctx.require(Perm.PURCHASE_EDIT)
    groups: dict[int | None, list[dict]] = defaultdict(list)
    for it in items:
        groups[it.get("supplier_id")].append(it)
    ids = []
    for supplier_id, lines in groups.items():
        note = "Черновик по рекомендациям программы. Проверьте количество и цены перед проведением."
        if any(line.get("last_price") is None for line in lines):
            note += " Для части строк цена неизвестна (0) — укажите цену."
        pid = purchasing.create_purchase(ctx, supplier_id, notes=note, source="recommendation")
        for line in lines:
            purchasing.add_purchase_line(ctx, pid, line["part_id"], line["recommended_qty"], line.get("last_price") or "0")
        ids.append(pid)
    with ctx.db.transaction() as s:
        s.add(AIRecommendation(user_id=ctx.user_id, kind="purchase", mode="basic",
                               input={"items": [{"part_id": i["part_id"], "qty": str(i["recommended_qty"])} for i in items]},
                               output={"purchase_ids": ids}, status="accepted"))
        audit(s, ctx, "recommendation.purchase_drafts", "purchase", None, purchase_ids=ids)
    return ids
