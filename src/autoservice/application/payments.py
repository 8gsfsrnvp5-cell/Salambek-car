"""Движение денег: оплаты клиентов, возвраты, оплаты поставщикам.

Деньги учитываются отдельно от выручки: оплата не делает заказ-наряд выданным,
а выданный заказ-наряд не считается оплаченным без записи об оплате.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoservice.application.context import AppContext, as_date, audit, get_or_404, next_number
from autoservice.domain.enums import DocStatus, PaymentKind, PaymentMethod, WOStatus
from autoservice.domain.errors import ValidationError
from autoservice.domain.money import ZERO, fmt_money, money, positive_money
from autoservice.persistence.models import Customer, Payment, PurchaseOrder, Sale, Supplier, WorkOrder, now
from autoservice.security.permissions import Perm


def _net(s: Session, column, value, plus: str, minus: str) -> Decimal:
    total = ZERO
    for p in s.execute(select(Payment).where(column == value, Payment.status == "posted")).scalars():
        if p.kind == plus:
            total += p.amount
        elif p.kind == minus:
            total -= p.amount
    return money(total)


def net_paid_sale(s: Session, sale_id: int) -> Decimal:
    return _net(s, Payment.sale_id, sale_id, PaymentKind.CUSTOMER_PAYMENT, PaymentKind.CUSTOMER_REFUND)


def net_paid_work_order(s: Session, wo_id: int) -> Decimal:
    return _net(s, Payment.work_order_id, wo_id, PaymentKind.CUSTOMER_PAYMENT, PaymentKind.CUSTOMER_REFUND)


def record_payment_in_session(
    s: Session,
    ctx: AppContext,
    *,
    kind: str,
    method: str,
    amount,
    customer_id: int | None = None,
    supplier_id: int | None = None,
    work_order_id: int | None = None,
    sale_id: int | None = None,
    purchase_id: int | None = None,
    sale_return_id: int | None = None,
    doc_date=None,
    note: str = "",
) -> Payment:
    if kind not in PaymentKind.TITLES:
        raise ValidationError("Неизвестный вид платежа")
    if method not in PaymentMethod.TITLES:
        raise ValidationError("Выберите способ оплаты: наличные, карта или перевод")
    amount = positive_money(amount, "Сумма платежа")
    links = [x for x in (work_order_id, sale_id, purchase_id) if x]
    if len(links) > 1:
        raise ValidationError("Платёж относится только к одному документу")

    if kind in (PaymentKind.CUSTOMER_PAYMENT, PaymentKind.CUSTOMER_REFUND):
        if purchase_id or supplier_id:
            raise ValidationError("Платёж клиента не может относиться к закупке")
        if sale_id:
            from autoservice.application.sales import returns_total

            sale = get_or_404(s, Sale, sale_id, "Продажа")
            if sale.status != DocStatus.POSTED:
                raise ValidationError("Оплату можно принять только по проведённой продаже")
            customer_id = customer_id or sale.customer_id
            paid = net_paid_sale(s, sale_id)
            due = money(sale.total_net - returns_total(s, sale_id))
            if kind == PaymentKind.CUSTOMER_PAYMENT and paid + amount > due:
                raise ValidationError(f"Сумма превышает остаток к оплате ({fmt_money(due - paid)})")
            if kind == PaymentKind.CUSTOMER_REFUND and amount > paid:
                raise ValidationError(f"Возврат больше полученной оплаты ({fmt_money(paid)})")
        elif work_order_id:
            wo = get_or_404(s, WorkOrder, work_order_id, "Заказ-наряд")
            if wo.status == WOStatus.CANCELLED and kind == PaymentKind.CUSTOMER_PAYMENT:
                raise ValidationError("Заказ-наряд отменён — оплата не принимается")
            customer_id = customer_id or wo.customer_id
            paid = net_paid_work_order(s, work_order_id)
            due = wo.total_amount if wo.status == WOStatus.ISSUED else max(wo.estimate_amount, wo.total_amount)
            if kind == PaymentKind.CUSTOMER_PAYMENT and paid + amount > due:
                raise ValidationError(f"Сумма превышает стоимость заказ-наряда (к оплате {fmt_money(max(due - paid, ZERO))})")
            if kind == PaymentKind.CUSTOMER_REFUND and amount > paid:
                raise ValidationError(f"Возврат больше полученной оплаты ({fmt_money(paid)})")
        elif kind == PaymentKind.CUSTOMER_REFUND:
            raise ValidationError("Возврат денег оформляется по конкретной продаже или заказ-наряду")
        if customer_id:
            get_or_404(s, Customer, customer_id, "Клиент")
    else:
        if not purchase_id:
            raise ValidationError("Платёж поставщику оформляется по конкретной закупке")
        from autoservice.application.purchasing import outstanding, paid_amount, returns_credit

        po = get_or_404(s, PurchaseOrder, purchase_id, "Закупка")
        if po.status != DocStatus.POSTED:
            raise ValidationError("Оплатить можно только проведённую закупку")
        supplier_id = po.supplier_id
        if kind == PaymentKind.SUPPLIER_PAYMENT and amount > outstanding(s, po):
            raise ValidationError(f"Сумма превышает долг по закупке ({fmt_money(outstanding(s, po))})")
        if kind == PaymentKind.SUPPLIER_REFUND:
            overpaid = paid_amount(s, po.id) - money(po.total - returns_credit(s, po.id))
            if amount > overpaid:
                raise ValidationError(f"Возврат от поставщика больше переплаты ({fmt_money(max(overpaid, ZERO))})")
        get_or_404(s, Supplier, supplier_id, "Поставщик")

    p = Payment(
        number=next_number(s, "payment", "ПЛ-", 6),
        doc_date=as_date(doc_date),
        kind=kind,
        direction=PaymentKind.DIRECTION[kind],
        method=method,
        amount=amount,
        customer_id=customer_id,
        supplier_id=supplier_id,
        work_order_id=work_order_id,
        sale_id=sale_id,
        purchase_id=purchase_id,
        sale_return_id=sale_return_id,
        note=note,
        created_by=ctx.user_id,
    )
    s.add(p)
    s.flush()
    audit(s, ctx, "payment.create", "payment", p.id, number=p.number, kind=kind, amount=str(amount), method=method)
    return p


def record_payment(ctx: AppContext, **kwargs) -> int:
    ctx.require(Perm.PAYMENT_CREATE)
    with ctx.db.transaction() as s:
        return record_payment_in_session(s, ctx, **kwargs).id


def cancel_payment(ctx: AppContext, payment_id: int, reason: str) -> None:
    """Ошибочный платёж аннулируется с причиной; запись остаётся в истории."""
    ctx.require(Perm.OPERATION_CANCEL)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину отмены платежа")
    with ctx.db.transaction() as s:
        p = get_or_404(s, Payment, payment_id, "Платёж")
        if p.status != "posted":
            raise ValidationError("Платёж уже отменён")
        if p.kind == PaymentKind.CUSTOMER_PAYMENT:
            paid_after = (net_paid_sale(s, p.sale_id) if p.sale_id else net_paid_work_order(s, p.work_order_id) if p.work_order_id else p.amount) - p.amount
            if (p.sale_id or p.work_order_id) and paid_after < 0:
                raise ValidationError("Сначала отмените возврат денег по этому документу")
        p.status = "cancelled"
        p.cancelled_at = now()
        p.cancelled_by = ctx.user_id
        p.cancel_reason = reason.strip()
        audit(s, ctx, "payment.cancel", "payment", p.id, number=p.number, amount=str(p.amount), reason=reason)


def list_payments(ctx: AppContext, date_from: date | None = None, date_to: date | None = None, direction: str | None = None,
                  include_cancelled: bool = True) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Payment).order_by(Payment.doc_date.desc(), Payment.id.desc())
        if date_from:
            stmt = stmt.where(Payment.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(Payment.doc_date <= date_to)
        if direction:
            stmt = stmt.where(Payment.direction == direction)
        if not include_cancelled:
            stmt = stmt.where(Payment.status == "posted")
        return [payment_dict(s, p) for p in s.execute(stmt).scalars()]


def payment_dict(s: Session, p: Payment) -> dict:
    doc = ""
    if p.work_order_id:
        doc = f"Заказ-наряд № {s.get(WorkOrder, p.work_order_id).number}"
    elif p.sale_id:
        doc = f"Продажа {s.get(Sale, p.sale_id).number}"
    elif p.purchase_id:
        doc = f"Закупка {s.get(PurchaseOrder, p.purchase_id).number}"
    party = ""
    if p.customer_id:
        party = s.get(Customer, p.customer_id).name
    elif p.supplier_id:
        party = s.get(Supplier, p.supplier_id).name
    return {
        "id": p.id,
        "number": p.number,
        "doc_date": p.doc_date,
        "kind": p.kind,
        "kind_title": PaymentKind.TITLES.get(p.kind, p.kind),
        "direction": p.direction,
        "method": p.method,
        "method_title": PaymentMethod.TITLES.get(p.method, p.method),
        "amount": p.amount,
        "signed_amount": p.amount if p.direction == "in" else -p.amount,
        "document": doc,
        "party": party,
        "note": p.note,
        "status": p.status,
        "status_title": "Проведён" if p.status == "posted" else "Отменён",
        "cancel_reason": p.cancel_reason,
    }
