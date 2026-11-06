"""Продажа запчастей клиенту: черновик → проведение (FIFO-списание) → возврат / отмена."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.context import AppContext, as_date, audit, get_or_404, next_number
from autoservice.application.pricing import discount_for, price_line
from autoservice.domain.enums import DocStatus, MovementKind, PaymentKind
from autoservice.domain.errors import AlreadyPosted, ValidationError
from autoservice.domain.money import ZERO, money, positive_qty
from autoservice.persistence.models import Customer, Part, Payment, Sale, SaleItem, SaleReturn, SaleReturnItem, now
from autoservice.security.permissions import Perm

DOC = "sale"


def _recalc(sale: Sale) -> None:
    sale.total_gross = money(sum((i.amount_gross for i in sale.items), ZERO))
    sale.total_discount = money(sum((i.discount_amount for i in sale.items), ZERO))
    sale.total_net = money(sale.total_gross - sale.total_discount)


def create_sale(ctx: AppContext, customer_id: int | None = None, customer_name: str = "", doc_date=None, notes: str = "") -> int:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        if customer_id:
            get_or_404(s, Customer, customer_id, "Клиент")
        sale = Sale(number=next_number(s, "sale", "ПР-", 6), doc_date=as_date(doc_date), customer_id=customer_id,
                    customer_name=(customer_name or "").strip(), notes=notes, created_by=ctx.user_id)
        s.add(sale)
        s.flush()
        audit(s, ctx, "sale.create", "sale", sale.id, number=sale.number)
        return sale.id


def _draft(s: Session, sale_id: int) -> Sale:
    sale = get_or_404(s, Sale, sale_id, "Продажа")
    if sale.status != DocStatus.DRAFT:
        raise AlreadyPosted(f"Продажа {sale.number} уже {DocStatus.TITLES[sale.status].lower()} — изменение запрещено")
    return sale


def update_sale_header(ctx: AppContext, sale_id: int, **fields) -> None:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        sale = _draft(s, sale_id)
        if "customer_id" in fields:
            if fields["customer_id"]:
                get_or_404(s, Customer, fields["customer_id"], "Клиент")
            sale.customer_id = fields["customer_id"]
        if "customer_name" in fields:
            sale.customer_name = (fields["customer_name"] or "").strip()
        if "doc_date" in fields:
            sale.doc_date = as_date(fields["doc_date"])
        if "notes" in fields:
            sale.notes = fields["notes"] or ""


def preview_line(ctx: AppContext, part_id: int, qty, markup_override=None, manual_unit_price=None) -> dict:
    """Предпросмотр строки для интерфейса: себестоимость, наценка, цена, прибыль."""
    q = positive_qty(qty)
    with ctx.db.read() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        lp = price_line(s, part, q, markup_override, manual_unit_price)
        profit = None if lp.cost_basis_total is None else money(lp.amount_gross - lp.cost_basis_total)
        return {
            "cost_basis_total": lp.cost_basis_total,
            "unit_cost": (lp.cost_basis_total / q) if lp.cost_basis_total is not None else None,
            "cost_estimated": lp.cost_estimated,
            "markup_pct": lp.markup_pct,
            "markup_source": lp.markup_source,
            "unit_price": lp.unit_price,
            "amount_gross": lp.amount_gross,
            "gross_profit": profit,
            "enough_stock": lp.enough_stock,
            "available": lp.available,
        }


def add_sale_line(ctx: AppContext, sale_id: int, part_id: int, qty, markup_override=None, manual_unit_price=None,
                  discount_percent=None, discount_amount=None) -> int:
    ctx.require(Perm.SALE_EDIT)
    q = positive_qty(qty)
    with ctx.db.transaction() as s:
        sale = _draft(s, sale_id)
        part = get_or_404(s, Part, part_id, "Запчасть")
        lp = price_line(s, part, q, markup_override, manual_unit_price)
        disc = discount_for(lp.amount_gross, discount_percent, discount_amount)
        item = SaleItem(
            line_no=max((i.line_no for i in sale.items), default=0) + 1,
            part_id=part.id,
            qty=q,
            cost_basis_total=lp.cost_basis_total,
            markup_pct=lp.markup_pct,
            markup_source=lp.markup_source,
            unit_price=lp.unit_price,
            amount_gross=lp.amount_gross,
            discount_amount=disc,
            amount_net=money(lp.amount_gross - disc),
        )
        sale.items.append(item)
        _recalc(sale)
        s.flush()
        return item.id


def update_sale_line(ctx: AppContext, item_id: int, qty=None, markup_override="keep", manual_unit_price=None,
                     discount_percent=None, discount_amount=None) -> None:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        item = get_or_404(s, SaleItem, item_id, "Строка продажи")
        sale = _draft(s, item.sale_id)
        q = positive_qty(qty) if qty is not None else item.qty
        markup = item.markup_pct if (markup_override == "keep" and item.markup_source == "operation") else (
            None if markup_override == "keep" else markup_override
        )
        manual = manual_unit_price if manual_unit_price is not None else (item.unit_price if item.markup_source == "manual" else None)
        lp = price_line(s, item.part, q, markup, manual)
        item.qty = q
        item.cost_basis_total = lp.cost_basis_total
        item.markup_pct = lp.markup_pct
        item.markup_source = lp.markup_source
        item.unit_price = lp.unit_price
        item.amount_gross = lp.amount_gross
        item.discount_amount = discount_for(lp.amount_gross, discount_percent, discount_amount)
        item.amount_net = money(item.amount_gross - item.discount_amount)
        _recalc(sale)


def remove_sale_line(ctx: AppContext, item_id: int) -> None:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        item = get_or_404(s, SaleItem, item_id, "Строка продажи")
        sale = _draft(s, item.sale_id)
        sale.items.remove(item)
        _recalc(sale)


def set_sale_discount(ctx: AppContext, sale_id: int, percent) -> None:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        sale = _draft(s, sale_id)
        for item in sale.items:
            item.discount_amount = discount_for(item.amount_gross, percent=percent)
            item.amount_net = money(item.amount_gross - item.discount_amount)
        _recalc(sale)


def delete_draft(ctx: AppContext, sale_id: int) -> None:
    ctx.require(Perm.SALE_EDIT)
    with ctx.db.transaction() as s:
        sale = _draft(s, sale_id)
        audit(s, ctx, "sale.delete_draft", "sale", sale.id, number=sale.number)
        s.delete(sale)


def post_sale(ctx: AppContext, sale_id: int, payment_method: str | None = None, payment_amount=None) -> None:
    """Проведение продажи: контроль остатка, FIFO-списание, себестоимость, (оплата) — одной транзакцией."""
    ctx.require(Perm.SALE_POST)
    from autoservice.application import payments

    with ctx.db.transaction() as s:
        sale = get_or_404(s, Sale, sale_id, "Продажа")
        if not sale.items:
            raise ValidationError("В продаже нет строк")
        result = s.execute(
            update(Sale)
            .where(Sale.id == sale_id, Sale.status == DocStatus.DRAFT)
            .values(status=DocStatus.POSTED, posted_at=now(), posted_by=ctx.user_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise AlreadyPosted(f"Продажа {sale.number} уже проведена или отменена")
        s.refresh(sale)
        total_cogs = ZERO
        complete = True
        for item in sale.items:
            cogs, _ = inventory.consume(s, ctx, item.part_id, item.qty, movement_kind=MovementKind.SALE_OUT,
                                        doc_type=DOC, doc_id=sale.id, line_id=item.id)
            item.cogs = cogs
            item.cogs_known = cogs is not None
            if cogs is None:
                complete = False
            else:
                total_cogs += cogs
        _recalc(sale)
        sale.total_cogs = money(total_cogs)  # известная часть; полнота — в cogs_complete
        sale.cogs_complete = complete
        audit(s, ctx, "sale.post", "sale", sale.id, number=sale.number, total=str(sale.total_net))
        if payment_method:
            amount = sale.total_net if payment_amount in (None, "") else payment_amount
            payments.record_payment_in_session(
                s, ctx, kind=PaymentKind.CUSTOMER_PAYMENT, method=payment_method, amount=amount, sale_id=sale.id,
                customer_id=sale.customer_id, doc_date=sale.doc_date,
            )


def cancel_sale(ctx: AppContext, sale_id: int, reason: str) -> None:
    """Отмена ошибочной продажи: товар возвращается в исходные партии, документ остаётся в истории."""
    ctx.require(Perm.OPERATION_CANCEL)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину отмены")
    from autoservice.application import payments

    with ctx.db.transaction() as s:
        sale = get_or_404(s, Sale, sale_id, "Продажа")
        if sale.status != DocStatus.POSTED:
            raise ValidationError("Отменить можно только проведённую продажу")
        if s.execute(select(SaleReturn).where(SaleReturn.sale_id == sale.id)).first():
            raise ValidationError("По продаже оформлен возврат — отмена невозможна")
        if payments.net_paid_sale(s, sale.id) != 0:
            raise ValidationError("По продаже есть оплата — сначала оформите возврат денег или отмените оплату")
        for item in sale.items:
            moves = inventory.movements_for(s, DOC, sale.id, item.id, {MovementKind.SALE_OUT})
            inventory.restore(s, ctx, moves, item.qty, movement_kind=MovementKind.SALE_CANCEL_IN, doc_type=DOC,
                              doc_id=sale.id, line_id=item.id, note=f"Отмена: {reason}")
        sale.status = DocStatus.CANCELLED
        sale.cancelled_at = now()
        sale.cancelled_by = ctx.user_id
        sale.cancel_reason = reason.strip()
        audit(s, ctx, "sale.cancel", "sale", sale.id, number=sale.number, reason=reason)


def create_return(ctx: AppContext, sale_id: int, items: list[tuple[int, object]], reason: str, refund_method: str | None = None,
                  doc_date=None) -> int:
    """Возврат от покупателя: товар — в исходные партии по исходной себестоимости,
    сумма возврата — пропорционально оплаченной цене строки (с учётом скидки)."""
    ctx.require(Perm.SALE_RETURN)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину возврата")
    from autoservice.application import payments

    with ctx.db.transaction() as s:
        sale = get_or_404(s, Sale, sale_id, "Продажа")
        if sale.status != DocStatus.POSTED:
            raise ValidationError("Возврат возможен только по проведённой продаже")
        ret = SaleReturn(number=next_number(s, "sale_return", "ВЗ-", 6), sale_id=sale.id, doc_date=as_date(doc_date),
                         reason=reason.strip(), created_by=ctx.user_id)
        s.add(ret)
        s.flush()
        total_refund = ZERO
        total_cogs: Decimal | None = ZERO
        for item_id, q in items:
            q = positive_qty(q)
            item = get_or_404(s, SaleItem, item_id, "Строка продажи")
            if item.sale_id != sale.id:
                raise ValidationError("Строка не относится к этой продаже")
            if item.returned_qty + q > item.qty:
                raise ValidationError(f"Нельзя вернуть больше, чем продано: «{item.part.name}»")
            moves = inventory.movements_for(s, DOC, sale.id, item.id, {MovementKind.SALE_OUT})
            cogs = inventory.restore(s, ctx, moves, q, movement_kind=MovementKind.SALE_RETURN_IN, doc_type="sale_return",
                                     doc_id=ret.id, line_id=item.id, note=reason)
            if item.returned_qty + q == item.qty:
                prior = sum(
                    (r.refund_amount for r in s.execute(select(SaleReturnItem).where(SaleReturnItem.sale_item_id == item.id)).scalars()),
                    ZERO,
                )
                refund = money(item.amount_net - prior)
            else:
                refund = money(item.amount_net * q / item.qty)
            item.returned_qty = item.returned_qty + q
            ret.items.append(SaleReturnItem(sale_item_id=item.id, qty=q, refund_amount=refund, cogs=cogs))
            total_refund += refund
            total_cogs = None if (total_cogs is None or cogs is None) else total_cogs + cogs
        if not ret.items:
            raise ValidationError("Не выбраны позиции для возврата")
        ret.total_refund = money(total_refund)
        ret.cogs_restored = money(total_cogs) if total_cogs is not None else None
        audit(s, ctx, "sale.return", "sale", sale.id, return_number=ret.number, refund=str(ret.total_refund))
        if refund_method:
            paid = payments.net_paid_sale(s, sale.id)
            refund_now = min(ret.total_refund, paid)
            if refund_now > 0:
                payments.record_payment_in_session(
                    s, ctx, kind=PaymentKind.CUSTOMER_REFUND, method=refund_method, amount=refund_now, sale_id=sale.id,
                    sale_return_id=ret.id, customer_id=sale.customer_id, doc_date=ret.doc_date,
                )
        return ret.id


# --------------------------------------------------------------------------- чтение


def returns_total(s: Session, sale_id: int) -> Decimal:
    return money(sum((r.total_refund for r in s.execute(select(SaleReturn).where(SaleReturn.sale_id == sale_id)).scalars()), ZERO))


def sale_dict(s: Session, sale: Sale) -> dict:
    from autoservice.application import payments

    paid = payments.net_paid_sale(s, sale.id)
    returned = returns_total(s, sale.id)
    due = money(sale.total_net - returned) if sale.status == DocStatus.POSTED else ZERO
    gp = None
    if sale.status == DocStatus.POSTED and sale.cogs_complete and sale.total_cogs is not None:
        gp = money(sale.total_net - sale.total_cogs)
    return {
        "id": sale.id,
        "number": sale.number,
        "doc_date": sale.doc_date,
        "customer_id": sale.customer_id,
        "customer": sale.customer.name if sale.customer else (sale.customer_name or "Розничный покупатель"),
        "status": sale.status,
        "status_title": DocStatus.TITLES.get(sale.status, sale.status),
        "total_gross": sale.total_gross,
        "total_discount": sale.total_discount,
        "total_net": sale.total_net,
        "total_cogs": sale.total_cogs,
        "cogs_complete": sale.cogs_complete,
        "gross_profit": gp,
        "returned": returned,
        "paid": paid,
        "debt": money(due - paid) if sale.status == DocStatus.POSTED else ZERO,
        "notes": sale.notes,
        "cancel_reason": sale.cancel_reason,
        "items": [
            {
                "id": i.id, "line_no": i.line_no, "part_id": i.part_id, "part_code": i.part.code, "part_name": i.part.name,
                "oem_number": i.part.oem_number, "brand": i.part.brand, "unit": i.part.unit, "qty": i.qty,
                "cost_basis_total": i.cost_basis_total, "markup_pct": i.markup_pct, "markup_source": i.markup_source,
                "unit_price": i.unit_price, "amount_gross": i.amount_gross, "discount_amount": i.discount_amount,
                "amount_net": i.amount_net, "cogs": i.cogs, "cogs_known": i.cogs_known, "returned_qty": i.returned_qty,
            }
            for i in sale.items
        ],
    }


def get_sale(ctx: AppContext, sale_id: int) -> dict:
    with ctx.db.read() as s:
        return sale_dict(s, get_or_404(s, Sale, sale_id, "Продажа"))


def list_sales(ctx: AppContext, date_from: date | None = None, date_to: date | None = None, status: str | None = None,
               query: str = "") -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Sale).order_by(Sale.doc_date.desc(), Sale.id.desc())
        if date_from:
            stmt = stmt.where(Sale.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(Sale.doc_date <= date_to)
        if status:
            stmt = stmt.where(Sale.status == status)
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.outerjoin(Customer, Customer.id == Sale.customer_id).where(
                func.lower(Sale.number).like(like) | Customer.name_search.like(like) | func.lower(Sale.customer_name).like(like)
            )
        out = []
        for sale in s.execute(stmt).scalars():
            d = sale_dict(s, sale)
            d.pop("items")
            out.append(d)
        return out


def returnable_items(ctx: AppContext, sale_id: int) -> list[dict]:
    with ctx.db.read() as s:
        sale = get_or_404(s, Sale, sale_id, "Продажа")
        return [
            {"id": i.id, "part_name": i.part.name, "qty": i.qty, "returned_qty": i.returned_qty, "can_return": i.qty - i.returned_qty}
            for i in sale.items
            if i.qty - i.returned_qty > 0
        ]
