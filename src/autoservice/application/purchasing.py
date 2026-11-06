"""Закупки: черновик → проведение (поступление на склад) → оплата / возврат / отмена."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.catalog import link_supplier
from autoservice.application.context import AppContext, as_date, audit, get_or_404, next_number, today
from autoservice.domain.enums import DocStatus, MovementKind, PaymentKind
from autoservice.domain.errors import AlreadyPosted, DuplicateDocument, ValidationError
from autoservice.domain.markup import validate_markup
from autoservice.domain.money import ZERO, allocate, money, non_negative_money, positive_qty
from autoservice.persistence.models import (
    Part,
    Payment,
    PriceHistory,
    PurchaseItem,
    PurchaseOrder,
    StockBatch,
    Supplier,
    SupplierReturn,
    SupplierReturnItem,
    now,
)
from autoservice.security.permissions import Perm

DOC = "purchase"


def _recalc(po: PurchaseOrder) -> None:
    goods = sum((i.amount for i in po.items), ZERO)
    po.total_goods = money(goods)
    po.total = money(goods + po.extra_costs)


def find_duplicate(s: Session, supplier_id: int | None, supplier_doc_number: str, exclude_id: int | None = None) -> PurchaseOrder | None:
    number = (supplier_doc_number or "").strip()
    if not supplier_id or not number:
        return None
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.supplier_id == supplier_id,
        func.lower(PurchaseOrder.supplier_doc_number) == number.lower(),
        PurchaseOrder.status != DocStatus.CANCELLED,
    )
    if exclude_id:
        stmt = stmt.where(PurchaseOrder.id != exclude_id)
    return s.execute(stmt).scalars().first()


def create_purchase(
    ctx: AppContext,
    supplier_id: int | None,
    supplier_doc_number: str = "",
    supplier_doc_date=None,
    doc_date=None,
    extra_costs="0",
    extra_costs_note: str = "",
    notes: str = "",
    source: str = "manual",
    invoice_document_id: int | None = None,
    allow_duplicate: bool = False,
) -> int:
    ctx.require(Perm.PURCHASE_EDIT)
    extra = non_negative_money(extra_costs or "0", "Дополнительные затраты")
    with ctx.db.transaction() as s:
        if supplier_id:
            get_or_404(s, Supplier, supplier_id, "Поставщик")
        dup = find_duplicate(s, supplier_id, supplier_doc_number)
        if dup and not allow_duplicate:
            raise DuplicateDocument(
                f"Документ поставщика № {supplier_doc_number} уже внесён (закупка {dup.number}). Повторный ввод заблокирован."
            )
        po = PurchaseOrder(
            number=next_number(s, "purchase", "ЗК-", 6),
            supplier_id=supplier_id,
            supplier_doc_number=(supplier_doc_number or "").strip(),
            supplier_doc_date=as_date(supplier_doc_date) if supplier_doc_date else None,
            doc_date=as_date(doc_date),
            status=DocStatus.DRAFT,
            extra_costs=extra,
            extra_costs_note=extra_costs_note,
            notes=notes,
            source=source,
            invoice_document_id=invoice_document_id,
            created_by=ctx.user_id,
        )
        s.add(po)
        s.flush()
        audit(s, ctx, "purchase.create", "purchase", po.id, number=po.number, duplicate_confirmed=bool(dup))
        return po.id


def _draft(s: Session, purchase_id: int) -> PurchaseOrder:
    po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
    if po.status != DocStatus.DRAFT:
        raise AlreadyPosted(f"Закупка {po.number} уже {DocStatus.TITLES[po.status].lower()} — изменение запрещено")
    return po


def update_purchase_header(ctx: AppContext, purchase_id: int, **fields) -> None:
    ctx.require(Perm.PURCHASE_EDIT)
    with ctx.db.transaction() as s:
        po = _draft(s, purchase_id)
        if "supplier_id" in fields:
            if fields["supplier_id"]:
                get_or_404(s, Supplier, fields["supplier_id"], "Поставщик")
            po.supplier_id = fields["supplier_id"]
        if "supplier_doc_number" in fields:
            po.supplier_doc_number = (fields["supplier_doc_number"] or "").strip()
        if "supplier_doc_date" in fields:
            po.supplier_doc_date = as_date(fields["supplier_doc_date"]) if fields["supplier_doc_date"] else None
        if "doc_date" in fields:
            po.doc_date = as_date(fields["doc_date"])
        if "extra_costs" in fields:
            po.extra_costs = non_negative_money(fields["extra_costs"] or "0", "Дополнительные затраты")
        for key in ("extra_costs_note", "notes"):
            if key in fields:
                setattr(po, key, fields[key] or "")
        _recalc(po)
        audit(s, ctx, "purchase.update", "purchase", po.id)


def add_purchase_line(
    ctx: AppContext,
    purchase_id: int,
    part_id: int,
    qty,
    unit_price,
    markup_pct=None,
    supplier_article: str = "",
    name_on_invoice: str = "",
) -> int:
    ctx.require(Perm.PURCHASE_EDIT)
    q = positive_qty(qty)
    price = non_negative_money(unit_price, "Цена закупки")
    markup = None if markup_pct in (None, "") else validate_markup(markup_pct)
    with ctx.db.transaction() as s:
        po = _draft(s, purchase_id)
        get_or_404(s, Part, part_id, "Запчасть")
        line_no = max((i.line_no for i in po.items), default=0) + 1
        item = PurchaseItem(
            line_no=line_no,
            part_id=part_id,
            qty=q,
            unit_price=price,
            amount=money(price * q),
            markup_pct=markup,
            supplier_article=(supplier_article or "").strip(),
            name_on_invoice=(name_on_invoice or "").strip(),
        )
        po.items.append(item)
        _recalc(po)
        s.flush()
        return item.id


def update_purchase_line(ctx: AppContext, item_id: int, qty=None, unit_price=None, markup_pct="keep") -> None:
    ctx.require(Perm.PURCHASE_EDIT)
    with ctx.db.transaction() as s:
        item = get_or_404(s, PurchaseItem, item_id, "Строка закупки")
        po = _draft(s, item.purchase_id)
        if qty is not None:
            item.qty = positive_qty(qty)
        if unit_price is not None:
            item.unit_price = non_negative_money(unit_price, "Цена закупки")
        if markup_pct != "keep":
            item.markup_pct = None if markup_pct in (None, "") else validate_markup(markup_pct)
        item.amount = money(item.unit_price * item.qty)
        _recalc(po)


def remove_purchase_line(ctx: AppContext, item_id: int) -> None:
    ctx.require(Perm.PURCHASE_EDIT)
    with ctx.db.transaction() as s:
        item = get_or_404(s, PurchaseItem, item_id, "Строка закупки")
        po = _draft(s, item.purchase_id)
        po.items.remove(item)
        _recalc(po)


def delete_draft(ctx: AppContext, purchase_id: int) -> None:
    """Черновик (не проведённый документ) можно удалить — он не влиял на учёт."""
    ctx.require(Perm.PURCHASE_EDIT)
    with ctx.db.transaction() as s:
        po = _draft(s, purchase_id)
        audit(s, ctx, "purchase.delete_draft", "purchase", po.id, number=po.number)
        s.delete(po)


def post_purchase(ctx: AppContext, purchase_id: int, allow_zero_price: bool = False) -> None:
    """Проведение: товар поступает на склад партиями. Повторное проведение невозможно."""
    ctx.require(Perm.PURCHASE_POST)
    with ctx.db.transaction() as s:
        po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
        if not po.supplier_id:
            raise ValidationError("Укажите поставщика перед проведением")
        if not po.items:
            raise ValidationError("В закупке нет строк")
        zero = [i.part.name for i in po.items if i.unit_price == 0]
        if zero and not allow_zero_price:
            raise ValidationError("Нулевая цена закупки: " + "; ".join(zero) + ". Укажите цену или подтвердите бесплатное поступление.")
        # Атомарная защита от повторного проведения: статус меняется только из «Черновик».
        result = s.execute(
            update(PurchaseOrder)
            .where(PurchaseOrder.id == purchase_id, PurchaseOrder.status == DocStatus.DRAFT)
            .values(status=DocStatus.POSTED, posted_at=now(), posted_by=ctx.user_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise AlreadyPosted(f"Закупка {po.number} уже проведена или отменена")
        s.refresh(po)
        _recalc(po)
        extras = allocate(po.extra_costs, [i.amount for i in po.items]) if po.extra_costs > 0 else [ZERO] * len(po.items)
        doc_day = po.doc_date or today()
        for item, extra in zip(po.items, extras):
            item.allocated_extra = extra
            cost = money(item.amount + extra)
            inventory.receive(
                s, ctx, item.part_id, item.qty, cost,
                source_kind="purchase", movement_kind=MovementKind.PURCHASE_IN, doc_type=DOC, doc_id=po.id,
                line_id=item.id, source_ref=po.number, markup=item.markup_pct, purchase_item_id=item.id,
            )
            link_supplier(s, item.part_id, po.supplier_id, item.supplier_article, item.unit_price, doc_day)
            s.add(PriceHistory(part_id=item.part_id, kind="purchase_cost", amount=item.unit_price,
                               source_doc=po.number, user_id=ctx.user_id))
        audit(s, ctx, "purchase.post", "purchase", po.id, number=po.number, total=str(po.total))


def cancel_purchase(ctx: AppContext, purchase_id: int, reason: str) -> None:
    """Отмена проведённой закупки возможна, только если товар ещё не расходовался."""
    ctx.require(Perm.OPERATION_CANCEL)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину отмены")
    with ctx.db.transaction() as s:
        po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
        if po.status != DocStatus.POSTED:
            raise ValidationError("Отменить можно только проведённую закупку")
        if paid_amount(s, po.id) != 0:
            raise ValidationError("По закупке есть оплаты поставщику — сначала отмените их или оформите возврат денег")
        if s.execute(select(SupplierReturn).where(SupplierReturn.purchase_id == po.id)).first():
            raise ValidationError("По закупке оформлены возвраты поставщику — отмена невозможна, используйте корректировку")
        for item in po.items:
            batch = s.execute(select(StockBatch).where(StockBatch.purchase_item_id == item.id)).scalar_one()
            if batch.qty_remaining != batch.qty_received:
                raise ValidationError(
                    f"Товар «{item.part.name}» из этой закупки уже продан или выдан в ремонт — отмена невозможна. "
                    "Оформите возврат поставщику на остаток."
                )
            inventory.remove_from_batch(s, ctx, batch, batch.qty_remaining, movement_kind=MovementKind.PURCHASE_CANCEL_OUT,
                                        doc_type=DOC, doc_id=po.id, line_id=item.id, note=reason)
        po.status = DocStatus.CANCELLED
        po.cancelled_at = now()
        po.cancelled_by = ctx.user_id
        po.cancel_reason = reason.strip()
        audit(s, ctx, "purchase.cancel", "purchase", po.id, number=po.number, reason=reason)


def supplier_return(ctx: AppContext, purchase_id: int, items: list[tuple[int, object]], reason: str, doc_date=None) -> int:
    """Возврат поставщику из партий этой закупки. Уменьшает долг перед поставщиком."""
    ctx.require(Perm.PURCHASE_POST)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину возврата")
    with ctx.db.transaction() as s:
        po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
        if po.status != DocStatus.POSTED:
            raise ValidationError("Возврат возможен только по проведённой закупке")
        ret = SupplierReturn(number=next_number(s, "supplier_return", "ВП-", 6), purchase_id=po.id,
                             doc_date=as_date(doc_date), reason=reason.strip(), created_by=ctx.user_id)
        s.add(ret)
        s.flush()
        total_credit = ZERO
        total_cost: Decimal | None = ZERO
        for item_id, q in items:
            q = positive_qty(q)
            item = get_or_404(s, PurchaseItem, item_id, "Строка закупки")
            if item.purchase_id != po.id:
                raise ValidationError("Строка не относится к этой закупке")
            batch = s.execute(select(StockBatch).where(StockBatch.purchase_item_id == item.id)).scalar_one()
            already = sum(
                (r.qty for r in s.execute(select(SupplierReturnItem).where(SupplierReturnItem.purchase_item_id == item.id)).scalars()),
                ZERO,
            )
            if already + q > item.qty:
                raise ValidationError("Количество возврата превышает количество в закупке")
            cost = inventory.remove_from_batch(s, ctx, batch, q, movement_kind=MovementKind.SUPPLIER_RETURN_OUT,
                                               doc_type="supplier_return", doc_id=ret.id, line_id=item.id, note=reason)
            if already + q == item.qty:
                prior_credit = sum(
                    (r.credit_amount for r in s.execute(select(SupplierReturnItem).where(SupplierReturnItem.purchase_item_id == item.id)).scalars()),
                    ZERO,
                )
                credit = money(item.amount - prior_credit)
            else:
                credit = money(item.amount * q / item.qty)
            ret.items.append(SupplierReturnItem(purchase_item_id=item.id, qty=q, credit_amount=credit, cost=cost))
            total_credit += credit
            total_cost = None if (total_cost is None or cost is None) else total_cost + cost
        if not ret.items:
            raise ValidationError("Не выбраны позиции для возврата")
        ret.total_credit = money(total_credit)
        ret.cost_removed = money(total_cost) if total_cost is not None else None
        audit(s, ctx, "purchase.supplier_return", "purchase", po.id, return_number=ret.number, credit=str(ret.total_credit))
        return ret.id


# --------------------------------------------------------------------------- расчёты с поставщиком


def paid_amount(s: Session, purchase_id: int) -> Decimal:
    total = ZERO
    for p in s.execute(select(Payment).where(Payment.purchase_id == purchase_id, Payment.status == "posted")).scalars():
        if p.kind == PaymentKind.SUPPLIER_PAYMENT:
            total += p.amount
        elif p.kind == PaymentKind.SUPPLIER_REFUND:
            total -= p.amount
    return money(total)


def returns_credit(s: Session, purchase_id: int) -> Decimal:
    value = sum(
        (r.total_credit for r in s.execute(select(SupplierReturn).where(SupplierReturn.purchase_id == purchase_id)).scalars()),
        ZERO,
    )
    return money(value)


def outstanding(s: Session, po: PurchaseOrder) -> Decimal:
    if po.status != DocStatus.POSTED:
        return ZERO
    return money(po.total - returns_credit(s, po.id) - paid_amount(s, po.id))


def payment_status(s: Session, po: PurchaseOrder) -> str:
    if po.status != DocStatus.POSTED:
        return "—"
    due = money(po.total - returns_credit(s, po.id))
    paid = paid_amount(s, po.id)
    if due <= 0 or paid >= due:
        return "Оплачено"
    if paid > 0:
        return "Частично оплачено"
    return "Не оплачено"


def purchase_dict(s: Session, po: PurchaseOrder) -> dict:
    return {
        "id": po.id,
        "number": po.number,
        "doc_date": po.doc_date,
        "supplier_id": po.supplier_id,
        "supplier": po.supplier.name if po.supplier else "",
        "supplier_doc_number": po.supplier_doc_number,
        "supplier_doc_date": po.supplier_doc_date,
        "status": po.status,
        "status_title": DocStatus.TITLES.get(po.status, po.status),
        "total_goods": po.total_goods,
        "extra_costs": po.extra_costs,
        "extra_costs_note": po.extra_costs_note,
        "total": po.total,
        "paid": paid_amount(s, po.id),
        "returns_credit": returns_credit(s, po.id),
        "outstanding": outstanding(s, po),
        "payment_status": payment_status(s, po),
        "notes": po.notes,
        "source": po.source,
        "invoice_document_id": po.invoice_document_id,
        "cancel_reason": po.cancel_reason,
        "items": [
            {
                "id": i.id, "line_no": i.line_no, "part_id": i.part_id, "part_code": i.part.code, "part_name": i.part.name,
                "oem_number": i.part.oem_number, "unit": i.part.unit, "qty": i.qty, "unit_price": i.unit_price,
                "amount": i.amount, "allocated_extra": i.allocated_extra, "markup_pct": i.markup_pct,
                "supplier_article": i.supplier_article, "name_on_invoice": i.name_on_invoice,
            }
            for i in po.items
        ],
    }


def get_purchase(ctx: AppContext, purchase_id: int) -> dict:
    with ctx.db.read() as s:
        return purchase_dict(s, get_or_404(s, PurchaseOrder, purchase_id, "Закупка"))


def list_purchases(ctx: AppContext, date_from: date | None = None, date_to: date | None = None, status: str | None = None,
                   supplier_id: int | None = None, query: str = "") -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(PurchaseOrder).order_by(PurchaseOrder.doc_date.desc(), PurchaseOrder.id.desc())
        if date_from:
            stmt = stmt.where(PurchaseOrder.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(PurchaseOrder.doc_date <= date_to)
        if status:
            stmt = stmt.where(PurchaseOrder.status == status)
        if supplier_id:
            stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(func.lower(PurchaseOrder.number).like(like) | func.lower(PurchaseOrder.supplier_doc_number).like(like))
        rows = []
        for po in s.execute(stmt).scalars():
            d = purchase_dict(s, po)
            d.pop("items")
            rows.append(d)
        return rows


def attach_invoice(ctx: AppContext, purchase_id: int, path) -> int:
    """Прикрепляет скан/файл накладной к закупке (не влияет на учёт, допускается в любом статусе)."""
    from autoservice.application.documents_store import store_file

    ctx.require(Perm.PURCHASE_EDIT)
    with ctx.db.transaction() as s:
        po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
        doc = store_file(s, ctx, path, "invoice", f"Накладная к {po.number}", purchase_id=po.id, supplier_id=po.supplier_id)
        po.invoice_document_id = doc.id
        audit(s, ctx, "purchase.attach_invoice", "purchase", po.id, document_id=doc.id)
        return doc.id
