"""Заказ-наряды: работы, запчасти (резерв → выдача → возврат), статусы, итоги."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.context import AppContext, audit, get_or_404, next_number
from autoservice.application.pricing import discount_for, price_line
from autoservice.application.settings_service import get_setting
from autoservice.domain.enums import MovementKind, PartLineStatus, WOStatus
from autoservice.domain.errors import InvalidStatusTransition, ValidationError
from autoservice.domain.money import ZERO, money, non_negative_money, positive_qty, qty as to_qty
from autoservice.domain.normalize import normalize_plate, normalize_text
from autoservice.persistence.models import (
    Customer,
    Employee,
    Part,
    StockReservation,
    Vehicle,
    WorkOrder,
    WorkOrderPart,
    WorkOrderService,
    now,
)
from autoservice.security.permissions import Perm

DOC = "work_order"
EDITABLE = WOStatus.OPEN - {WOStatus.READY}


def _editable(s: Session, wo_id: int) -> WorkOrder:
    wo = get_or_404(s, WorkOrder, wo_id, "Заказ-наряд")
    if wo.status not in EDITABLE:
        if wo.status == WOStatus.READY:
            raise ValidationError("Заказ-наряд в статусе «Готов». Для изменений верните его в статус «В ремонте».")
        raise ValidationError(f"Заказ-наряд в статусе «{WOStatus.TITLES[wo.status]}» — изменения запрещены")
    return wo


def _line_reserved(s: Session, line_id: int) -> Decimal:
    rows = s.execute(select(StockReservation).where(StockReservation.work_order_part_id == line_id, StockReservation.status == "active")).scalars()
    return sum((r.qty for r in rows), ZERO)


def _release(s: Session, line_id: int, status: str = "released") -> None:
    for r in s.execute(select(StockReservation).where(StockReservation.work_order_part_id == line_id, StockReservation.status == "active")).scalars():
        r.status = status


def _reserve(s: Session, line: WorkOrderPart) -> Decimal:
    """Резервирует недостающее до количества строки, сколько позволяет свободный остаток."""
    need = line.qty - line.issued_qty - _line_reserved(s, line.id)
    if need <= 0:
        return _line_reserved(s, line.id)
    avail = inventory.available(s, line.part_id)
    take = min(need, max(avail, ZERO))
    if take > 0:
        s.add(StockReservation(part_id=line.part_id, work_order_part_id=line.id, qty=take, status="active"))
        s.flush()
    reserved = _line_reserved(s, line.id)
    if line.issued_qty == 0:
        line.status = PartLineStatus.RESERVED if reserved >= line.qty else PartLineStatus.PLANNED
    return reserved


def _recalc(s: Session, wo: WorkOrder) -> None:
    s.flush()
    services_gross = ZERO
    parts_gross = ZERO
    discount = ZERO
    estimate = ZERO
    cogs = ZERO
    complete = True
    for x in wo.services:
        estimate += x.amount_net
        if x.is_done:
            services_gross += x.amount_gross
            discount += x.discount_amount
    for p in wo.parts:
        if p.qty <= 0:
            continue
        estimate += p.amount_net
        if p.issued_qty == p.qty:
            parts_gross += p.amount_gross
            discount += p.discount_amount
            if p.cogs_known and p.cogs is not None:
                cogs += p.cogs
            else:
                complete = False
    wo.services_amount = money(services_gross)
    wo.parts_amount = money(parts_gross)
    wo.discount_amount = money(discount)
    wo.total_amount = money(services_gross + parts_gross - discount)
    wo.estimate_amount = money(estimate)
    wo.cogs_amount = money(cogs)
    wo.cogs_complete = complete


# --------------------------------------------------------------------------- шапка


def create_work_order(ctx: AppContext, vehicle_id: int, complaint: str = "", mileage=None, customer_id: int | None = None,
                      mechanic_id: int | None = None) -> int:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        v = get_or_404(s, Vehicle, vehicle_id, "Автомобиль")
        customer_id = customer_id or v.owner_id
        if customer_id:
            get_or_404(s, Customer, customer_id, "Клиент")
        if mechanic_id:
            get_or_404(s, Employee, mechanic_id, "Сотрудник")
        m = int(str(mileage).replace(" ", "")) if mileage not in (None, "") else None
        if m is not None and m < 0:
            raise ValidationError("Пробег не может быть отрицательным")
        wo = WorkOrder(number=next_number(s, "work_order", "", 6), vehicle_id=v.id, customer_id=customer_id, mileage=m,
                       complaint=(complaint or "").strip(), mechanic_id=mechanic_id, status=WOStatus.NEW, created_by=ctx.user_id)
        s.add(wo)
        s.flush()
        audit(s, ctx, "work_order.create", "work_order", wo.id, number=wo.number, vehicle_id=v.id)
        return wo.id


def update_work_order(ctx: AppContext, wo_id: int, **fields) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        wo = get_or_404(s, WorkOrder, wo_id, "Заказ-наряд")
        if wo.status in (WOStatus.ISSUED, WOStatus.CANCELLED):
            raise ValidationError("Закрытый заказ-наряд не изменяется")
        for key in ("complaint", "diagnosis", "recommendations", "notes"):
            if key in fields:
                setattr(wo, key, (fields[key] or "").strip())
        if "mileage" in fields:
            m = fields["mileage"]
            wo.mileage = int(str(m).replace(" ", "")) if m not in (None, "") else None
            if wo.mileage is not None and wo.mileage < 0:
                raise ValidationError("Пробег не может быть отрицательным")
        if "mechanic_id" in fields:
            if fields["mechanic_id"]:
                get_or_404(s, Employee, fields["mechanic_id"], "Сотрудник")
            wo.mechanic_id = fields["mechanic_id"] or None
        if "customer_id" in fields:
            if fields["customer_id"]:
                get_or_404(s, Customer, fields["customer_id"], "Клиент")
            wo.customer_id = fields["customer_id"] or None
        audit(s, ctx, "work_order.update", "work_order", wo.id, fields=sorted(fields))


# --------------------------------------------------------------------------- работы


def add_service(ctx: AppContext, wo_id: int, name: str, unit_price=None, qty="1", hours=None, employee_id: int | None = None,
                done: bool = False, discount_percent=None) -> int:
    """Работа: цена × количество. Если указаны нормо-часы без цены — цена = стоимость нормо-часа из настроек."""
    ctx.require(Perm.WORKORDER_EDIT)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Укажите наименование работы")
    with ctx.db.transaction() as s:
        wo = _editable(s, wo_id)
        h = to_qty(hours) if hours not in (None, "") else None
        q = positive_qty(qty or "1")
        if unit_price in (None, ""):
            rate = get_setting(s, "pricing.labor_rate")
            if h is None or rate in (None, ""):
                raise ValidationError("Укажите стоимость работы (или нормо-часы и стоимость нормо-часа в настройках)")
            price = non_negative_money(rate, "Стоимость нормо-часа")
            q = positive_qty(h)
        else:
            price = non_negative_money(unit_price, "Стоимость работы")
        if employee_id:
            get_or_404(s, Employee, employee_id, "Сотрудник")
        gross = money(price * q)
        disc = discount_for(gross, discount_percent)
        line = WorkOrderService(
            line_no=max((x.line_no for x in wo.services), default=0) + 1, name=name, employee_id=employee_id or wo.mechanic_id,
            hours=h, qty=q, unit_price=price, amount_gross=gross, discount_amount=disc, amount_net=money(gross - disc),
            is_done=bool(done), done_at=now() if done else None,
        )
        wo.services.append(line)
        _recalc(s, wo)
        audit(s, ctx, "work_order.service_add", "work_order", wo.id, name=name, price=str(price))
        return line.id


def update_service(ctx: AppContext, line_id: int, **fields) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderService, line_id, "Работа")
        wo = _editable(s, line.work_order_id)
        if "name" in fields:
            if not (fields["name"] or "").strip():
                raise ValidationError("Укажите наименование работы")
            line.name = fields["name"].strip()
        if "unit_price" in fields:
            line.unit_price = non_negative_money(fields["unit_price"], "Стоимость работы")
        if "qty" in fields:
            line.qty = positive_qty(fields["qty"])
        if "employee_id" in fields:
            if fields["employee_id"]:
                get_or_404(s, Employee, fields["employee_id"], "Сотрудник")
            line.employee_id = fields["employee_id"] or None
        line.amount_gross = money(line.unit_price * line.qty)
        if "discount_percent" in fields:
            line.discount_amount = discount_for(line.amount_gross, fields["discount_percent"])
        line.discount_amount = min(line.discount_amount, line.amount_gross)
        line.amount_net = money(line.amount_gross - line.discount_amount)
        _recalc(s, wo)


def set_service_done(ctx: AppContext, line_id: int, done: bool = True, employee_id: int | None = None) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderService, line_id, "Работа")
        wo = _editable(s, line.work_order_id)
        line.is_done = done
        line.done_at = now() if done else None
        if employee_id:
            get_or_404(s, Employee, employee_id, "Сотрудник")
            line.employee_id = employee_id
        _recalc(s, wo)
        audit(s, ctx, "work_order.service_done" if done else "work_order.service_undone", "work_order", wo.id, line=line.name)


def remove_service(ctx: AppContext, line_id: int) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderService, line_id, "Работа")
        wo = _editable(s, line.work_order_id)
        audit(s, ctx, "work_order.service_remove", "work_order", wo.id, name=line.name)
        wo.services.remove(line)
        _recalc(s, wo)


# --------------------------------------------------------------------------- запчасти


def add_part(ctx: AppContext, wo_id: int, part_id: int, qty, markup_override=None, manual_unit_price=None,
             is_consumable: bool = False, reserve: bool = True, discount_percent=None) -> dict:
    ctx.require(Perm.WORKORDER_EDIT)
    q = positive_qty(qty)
    with ctx.db.transaction() as s:
        wo = _editable(s, wo_id)
        part = get_or_404(s, Part, part_id, "Запчасть")
        lp = price_line(s, part, q, markup_override, manual_unit_price)
        disc = discount_for(lp.amount_gross, discount_percent)
        line = WorkOrderPart(
            line_no=max((x.line_no for x in wo.parts), default=0) + 1, part_id=part.id, qty=q, is_consumable=is_consumable,
            cost_basis_total=lp.cost_basis_total, markup_pct=lp.markup_pct, markup_source=lp.markup_source,
            unit_price=lp.unit_price, amount_gross=lp.amount_gross, discount_amount=disc, amount_net=money(lp.amount_gross - disc),
            status=PartLineStatus.PLANNED,
        )
        wo.parts.append(line)
        s.flush()
        reserved = _reserve(s, line) if reserve else ZERO
        _recalc(s, wo)
        audit(s, ctx, "work_order.part_add", "work_order", wo.id, part_id=part.id, qty=str(q), price=str(lp.unit_price))
        return {"id": line.id, "reserved": reserved, "shortage": max(q - reserved, ZERO), "cost_estimated": lp.cost_estimated}


def update_part_qty(ctx: AppContext, line_id: int, qty) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    q = positive_qty(qty)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        wo = _editable(s, line.work_order_id)
        if line.issued_qty > 0:
            raise ValidationError("Запчасть уже выдана со склада — для уменьшения оформите возврат на склад")
        _release(s, line.id)
        markup = line.markup_pct if line.markup_source == "operation" else None
        manual = line.unit_price if line.markup_source == "manual" else None
        lp = price_line(s, line.part, q, markup, manual, exclude_line_id=line.id)
        line.qty = q
        line.cost_basis_total = lp.cost_basis_total
        line.markup_pct = lp.markup_pct
        line.markup_source = lp.markup_source
        line.unit_price = lp.unit_price
        line.amount_gross = lp.amount_gross
        line.discount_amount = min(line.discount_amount, line.amount_gross)
        line.amount_net = money(line.amount_gross - line.discount_amount)
        _reserve(s, line)
        _recalc(s, wo)


def reserve_part(ctx: AppContext, line_id: int) -> Decimal:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        _editable(s, line.work_order_id)
        return _reserve(s, line)


def release_part(ctx: AppContext, line_id: int) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        _editable(s, line.work_order_id)
        _release(s, line.id)
        if line.issued_qty == 0:
            line.status = PartLineStatus.PLANNED


def issue_part(ctx: AppContext, line_id: int) -> Decimal | None:
    """Выдача запчасти со склада в ремонт (списание FIFO). Снимает резерв строки."""
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        wo = _editable(s, line.work_order_id)
        to_issue = line.qty - line.issued_qty
        if to_issue <= 0:
            raise ValidationError("Запчасть уже выдана полностью")
        cogs, _ = inventory.consume(s, ctx, line.part_id, to_issue, movement_kind=MovementKind.WO_ISSUE, doc_type=DOC,
                                    doc_id=wo.id, line_id=line.id, own_reservation_line_id=line.id)
        _release(s, line.id, status="consumed")
        line.issued_qty = line.qty
        line.issued_at = now()
        line.status = PartLineStatus.ISSUED
        if cogs is None:
            line.cogs_known = False
        else:
            line.cogs = money((line.cogs or ZERO) + cogs)
        if wo.status in (WOStatus.NEW, WOStatus.DIAGNOSTICS, WOStatus.AWAITING_APPROVAL, WOStatus.AWAITING_PARTS):
            wo.status = WOStatus.IN_PROGRESS
        _recalc(s, wo)
        audit(s, ctx, "work_order.part_issue", "work_order", wo.id, part_id=line.part_id, qty=str(to_issue),
              cogs=str(cogs) if cogs is not None else None)
        return cogs


def return_part(ctx: AppContext, line_id: int, qty, reason: str = "") -> Decimal | None:
    """Возврат неиспользованной запчасти на склад (в исходные партии)."""
    ctx.require(Perm.WORKORDER_EDIT)
    q = positive_qty(qty)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        wo = _editable(s, line.work_order_id)
        if q > line.issued_qty:
            raise ValidationError("Нельзя вернуть больше, чем выдано со склада")
        moves = inventory.movements_for(s, DOC, wo.id, line.id, {MovementKind.WO_ISSUE})
        restored = inventory.restore(s, ctx, moves, q, movement_kind=MovementKind.WO_RETURN_IN, doc_type=DOC, doc_id=wo.id,
                                     line_id=line.id, note=reason or "Возврат из ремонта")
        line.issued_qty = line.issued_qty - q
        line.returned_qty = line.returned_qty + q
        line.qty = line.qty - q
        if line.cogs is not None and restored is not None:
            line.cogs = money(line.cogs - restored)
        line.amount_gross = money(line.unit_price * line.qty)
        line.discount_amount = min(line.discount_amount, line.amount_gross)
        line.amount_net = money(line.amount_gross - line.discount_amount)
        if line.qty == 0:
            line.status = PartLineStatus.RETURNED
        elif line.issued_qty == line.qty:
            line.status = PartLineStatus.ISSUED
        _recalc(s, wo)
        audit(s, ctx, "work_order.part_return", "work_order", wo.id, part_id=line.part_id, qty=str(q), reason=reason)
        return restored


def remove_part(ctx: AppContext, line_id: int) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        line = get_or_404(s, WorkOrderPart, line_id, "Строка запчасти")
        wo = _editable(s, line.work_order_id)
        if line.issued_qty > 0 or line.returned_qty > 0:
            raise ValidationError("По строке были складские движения — удалить нельзя, оформите возврат на склад")
        for r in s.execute(select(StockReservation).where(StockReservation.work_order_part_id == line.id)).scalars():
            s.delete(r)
        audit(s, ctx, "work_order.part_remove", "work_order", wo.id, part_id=line.part_id)
        wo.parts.remove(line)
        _recalc(s, wo)


def set_discount(ctx: AppContext, wo_id: int, percent) -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    with ctx.db.transaction() as s:
        wo = _editable(s, wo_id)
        for line in list(wo.services) + [p for p in wo.parts if p.qty > 0]:
            line.discount_amount = discount_for(line.amount_gross, percent=percent)
            line.amount_net = money(line.amount_gross - line.discount_amount)
        _recalc(s, wo)
        audit(s, ctx, "work_order.discount", "work_order", wo.id, percent=str(percent))


# --------------------------------------------------------------------------- статусы


def change_status(ctx: AppContext, wo_id: int, new_status: str, reason: str = "") -> None:
    ctx.require(Perm.WORKORDER_EDIT)
    from autoservice.application.payments import net_paid_work_order

    with ctx.db.transaction() as s:
        wo = get_or_404(s, WorkOrder, wo_id, "Заказ-наряд")
        old = wo.status
        if new_status not in WOStatus.TITLES:
            raise ValidationError("Неизвестный статус")
        if new_status == old:
            return
        if new_status not in WOStatus.TRANSITIONS.get(old, set()):
            raise InvalidStatusTransition(
                f"Переход «{WOStatus.TITLES[old]}» → «{WOStatus.TITLES[new_status]}» не допускается"
            )
        _recalc(s, wo)
        if new_status == WOStatus.READY:
            not_done = [x.name for x in wo.services if not x.is_done]
            if not_done:
                raise ValidationError("Есть невыполненные работы: " + "; ".join(not_done) + ". Отметьте их выполненными или удалите.")
            not_issued = [p.part.name for p in wo.parts if p.qty > 0 and p.issued_qty < p.qty]
            if not_issued:
                raise ValidationError(
                    "Запчасти не выданы со склада: " + "; ".join(not_issued) + ". Выдайте их или удалите из заказ-наряда."
                )
            if not any(x.is_done for x in wo.services) and not any(p.qty > 0 for p in wo.parts):
                raise ValidationError("В заказ-наряде нет выполненных работ и установленных запчастей")
            wo.ready_at = now()
        elif new_status == WOStatus.ISSUED:
            wo.closed_at = now()
            v = s.get(Vehicle, wo.vehicle_id)
            if wo.mileage and (v.mileage is None or wo.mileage > v.mileage):
                v.mileage = wo.mileage
        elif new_status == WOStatus.CANCELLED:
            if not (reason or "").strip():
                raise ValidationError("Укажите причину отмены заказ-наряда")
            issued = [p.part.name for p in wo.parts if p.issued_qty > 0]
            if issued:
                raise ValidationError("Запчасти выданы со склада: " + "; ".join(issued) + ". Сначала верните их на склад.")
            if net_paid_work_order(s, wo.id) != 0:
                raise ValidationError("По заказ-наряду есть оплата — сначала оформите возврат денег клиенту")
            for p in wo.parts:
                _release(s, p.id)
            wo.cancelled_at = now()
            wo.cancel_reason = reason.strip()
        wo.status = new_status
        audit(s, ctx, "work_order.status", "work_order", wo.id, old=old, new=new_status, reason=reason)


def reopen_issued(ctx: AppContext, wo_id: int, reason: str) -> None:
    """Исправление выданного заказ-наряда (только администратор): «Выдан» → «Готов»."""
    ctx.require(Perm.OPERATION_CANCEL)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину")
    with ctx.db.transaction() as s:
        wo = get_or_404(s, WorkOrder, wo_id, "Заказ-наряд")
        if wo.status != WOStatus.ISSUED:
            raise ValidationError("Заказ-наряд не выдан")
        wo.status = WOStatus.READY
        wo.closed_at = None
        audit(s, ctx, "work_order.reopen", "work_order", wo.id, reason=reason)


# --------------------------------------------------------------------------- чтение


def work_order_dict(s: Session, wo: WorkOrder) -> dict:
    from autoservice.application.payments import net_paid_work_order, payment_dict
    from autoservice.application.vehicles import vehicle_title
    from autoservice.persistence.models import Payment

    paid = net_paid_work_order(s, wo.id)
    closed = wo.status == WOStatus.ISSUED
    due = wo.total_amount if closed else ZERO
    reserved = {p.id: _line_reserved(s, p.id) for p in wo.parts}
    gp = None
    if closed and wo.cogs_complete and wo.cogs_amount is not None:
        gp = money(wo.total_amount - wo.cogs_amount)
    return {
        "id": wo.id,
        "number": wo.number,
        "opened_at": wo.opened_at,
        "closed_at": wo.closed_at,
        "status": wo.status,
        "status_title": WOStatus.TITLES.get(wo.status, wo.status),
        "allowed_statuses": sorted(WOStatus.TRANSITIONS.get(wo.status, set()), key=WOStatus.ORDER.index),
        "vehicle_id": wo.vehicle_id,
        "vehicle": vehicle_title(wo.vehicle),
        "plate": wo.vehicle.plate,
        "vin": wo.vehicle.vin or "",
        "vehicle_make": wo.vehicle.make,
        "vehicle_model": wo.vehicle.model,
        "vehicle_year": wo.vehicle.year,
        "engine_code": wo.vehicle.engine_code,
        "customer_id": wo.customer_id,
        "customer": wo.customer.name if wo.customer else "",
        "customer_phone": wo.customer.phone if wo.customer else "",
        "mileage": wo.mileage,
        "complaint": wo.complaint,
        "diagnosis": wo.diagnosis,
        "recommendations": wo.recommendations,
        "notes": wo.notes,
        "mechanic_id": wo.mechanic_id,
        "mechanic": wo.mechanic.full_name if wo.mechanic else "",
        "services_amount": wo.services_amount,
        "parts_amount": wo.parts_amount,
        "discount_amount": wo.discount_amount,
        "total_amount": wo.total_amount,
        "estimate_amount": wo.estimate_amount,
        "cogs_amount": wo.cogs_amount,
        "cogs_complete": wo.cogs_complete,
        "gross_profit": gp,
        "paid": paid,
        "debt": money(due - paid) if closed else ZERO,
        "to_pay": money(max((wo.total_amount if closed else wo.estimate_amount) - paid, ZERO)),
        "cancel_reason": wo.cancel_reason,
        "editable": wo.status in EDITABLE,
        "services": [
            {"id": x.id, "line_no": x.line_no, "name": x.name, "employee_id": x.employee_id,
             "employee": x.employee.full_name if x.employee else "", "hours": x.hours, "qty": x.qty, "unit_price": x.unit_price,
             "amount_gross": x.amount_gross, "discount_amount": x.discount_amount, "amount_net": x.amount_net,
             "is_done": x.is_done, "done_at": x.done_at}
            for x in wo.services
        ],
        "parts": [
            {"id": p.id, "line_no": p.line_no, "part_id": p.part_id, "part_code": p.part.code, "part_name": p.part.name,
             "oem_number": p.part.oem_number, "brand": p.part.brand, "unit": p.part.unit, "qty": p.qty,
             "is_consumable": p.is_consumable, "cost_basis_total": p.cost_basis_total, "markup_pct": p.markup_pct,
             "markup_source": p.markup_source, "unit_price": p.unit_price, "amount_gross": p.amount_gross,
             "discount_amount": p.discount_amount, "amount_net": p.amount_net, "status": p.status,
             "status_title": PartLineStatus.TITLES.get(p.status, p.status), "reserved_qty": reserved[p.id],
             "issued_qty": p.issued_qty, "returned_qty": p.returned_qty, "cogs": p.cogs, "cogs_known": p.cogs_known}
            for p in wo.parts
        ],
        "payments": [
            payment_dict(s, p) for p in s.execute(select(Payment).where(Payment.work_order_id == wo.id).order_by(Payment.id)).scalars()
        ],
    }


def get_work_order(ctx: AppContext, wo_id: int) -> dict:
    with ctx.db.read() as s:
        return work_order_dict(s, get_or_404(s, WorkOrder, wo_id, "Заказ-наряд"))


def list_work_orders(ctx: AppContext, statuses: set[str] | None = None, query: str = "", date_from: date | None = None,
                     date_to: date | None = None, vehicle_id: int | None = None) -> list[dict]:
    from autoservice.application.payments import net_paid_work_order
    from autoservice.application.vehicles import vehicle_title

    with ctx.db.read() as s:
        stmt = select(WorkOrder).join(Vehicle, Vehicle.id == WorkOrder.vehicle_id).order_by(WorkOrder.opened_at.desc(), WorkOrder.id.desc())
        if statuses:
            stmt = stmt.where(WorkOrder.status.in_(statuses))
        if vehicle_id:
            stmt = stmt.where(WorkOrder.vehicle_id == vehicle_id)
        if date_from:
            stmt = stmt.where(func.date(WorkOrder.opened_at) >= date_from.isoformat())
        if date_to:
            stmt = stmt.where(func.date(WorkOrder.opened_at) <= date_to.isoformat())
        q = (query or "").strip()
        if q:
            text = f"%{normalize_text(q)}%"
            stmt = stmt.outerjoin(Customer, Customer.id == WorkOrder.customer_id).where(
                or_(
                    func.lower(WorkOrder.number).like(f"%{q.lower()}%"),
                    Vehicle.plate_normalized.like(f"%{normalize_plate(q)}%"),
                    Vehicle.vin.like(f"%{q.upper()}%"),
                    func.lower(Vehicle.make + " " + Vehicle.model).like(text),
                    Customer.name_search.like(text),
                )
            )
        out = []
        for wo in s.execute(stmt).scalars():
            paid = net_paid_work_order(s, wo.id)
            closed = wo.status == WOStatus.ISSUED
            out.append({
                "id": wo.id, "number": wo.number, "opened_at": wo.opened_at, "closed_at": wo.closed_at, "status": wo.status,
                "status_title": WOStatus.TITLES.get(wo.status, wo.status), "vehicle": vehicle_title(wo.vehicle),
                "plate": wo.vehicle.plate, "customer": wo.customer.name if wo.customer else "", "complaint": wo.complaint,
                "total": wo.total_amount if closed else wo.estimate_amount, "paid": paid,
                "debt": money(wo.total_amount - paid) if closed else ZERO,
                "mechanic": wo.mechanic.full_name if wo.mechanic else "",
            })
        return out


def service_suggestions(ctx: AppContext, prefix: str = "", limit: int = 30) -> list[dict]:
    """Подсказки работ из истории (название и последняя цена) — для быстрого ввода."""
    with ctx.db.read() as s:
        stmt = select(WorkOrderService.name, WorkOrderService.unit_price, WorkOrderService.id).order_by(WorkOrderService.id.desc())
        if prefix:
            stmt = stmt.where(func.lower(WorkOrderService.name).like(f"%{prefix.strip().lower()}%"))
        seen = {}
        for name, price, _ in s.execute(stmt.limit(2000)).all():
            key = name.strip().lower()
            if key not in seen:
                seen[key] = {"name": name, "last_price": price}
            if len(seen) >= limit:
                break
        return list(seen.values())
