"""Отчёты. Строятся только по проведённым операциям.

Признание выручки: продажа — дата документа; заказ-наряд — дата выдачи
(статус «Выдан»); возврат — дата возврата. Деньги — по датам платежей.
Это управленческий учёт, а не бухгалтерская или налоговая отчётность.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.context import AppContext
from autoservice.application.settings_service import VAT_MODES, get_all
from autoservice.domain.enums import DocStatus, MovementKind, PaymentKind, PaymentMethod, WOStatus
from autoservice.domain.money import ZERO, money
from autoservice.domain.normalize import normalize_article
from autoservice.persistence.models import (
    Customer,
    Expense,
    InventoryCount,
    InventoryCountItem,
    Part,
    PartCategory,
    Payment,
    PurchaseItem,
    PurchaseOrder,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
    StockAdjustment,
    StockMovement,
    Supplier,
    SupplierReturn,
    Vehicle,
    WorkOrder,
    WorkOrderPart,
    WorkOrderService,
)
from autoservice.security.permissions import Perm


@dataclass
class Column:
    key: str
    title: str
    kind: str = "text"  # text | money | qty | date | datetime | percent | int
    width: int = 14


@dataclass
class ReportFilters:
    date_from: date | None = None
    date_to: date | None = None
    supplier_id: int | None = None
    category_id: int | None = None
    article: str = ""
    work_order_number: str = ""

    def period(self) -> tuple[date, date]:
        today = date.today()
        return (self.date_from or date(today.year, today.month, 1), self.date_to or today)


@dataclass
class ReportTable:
    code: str
    title: str
    columns: list[Column]
    rows: list[dict]
    totals: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    period: tuple[date, date] | None = None
    filters_text: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))


def _in_period_dt(column, f: ReportFilters):
    d1, d2 = f.period()
    return (column >= datetime.combine(d1, datetime.min.time()), column < datetime.combine(d2 + timedelta(days=1), datetime.min.time()))


def _part_filter_ok(s: Session, part: Part, f: ReportFilters) -> bool:
    if f.category_id and part.category_id != f.category_id:
        return False
    if f.article:
        norm = normalize_article(f.article)
        if norm not in part.oem_number_normalized and norm not in normalize_article(part.code):
            return False
    return True


def _cats(s: Session) -> dict[int, str]:
    return dict(s.execute(select(PartCategory.id, PartCategory.name)).all())


def _sum(rows, key):
    return money(sum((r[key] for r in rows if r.get(key) is not None), ZERO))


# --------------------------------------------------------------------------- 1. закупки


def report_purchases(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    stmt = (
        select(PurchaseItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseItem.purchase_id)
        .where(PurchaseOrder.status == DocStatus.POSTED, PurchaseOrder.doc_date >= d1, PurchaseOrder.doc_date <= d2)
        .order_by(PurchaseOrder.doc_date, PurchaseOrder.id, PurchaseItem.line_no)
    )
    if f.supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == f.supplier_id)
    cats = _cats(s)
    rows = []
    for item, po in s.execute(stmt).all():
        part = item.part
        if not _part_filter_ok(s, part, f):
            continue
        rows.append({
            "date": po.doc_date, "number": po.number, "supplier": po.supplier.name if po.supplier else "",
            "supplier_doc": po.supplier_doc_number, "article": part.oem_number, "name": part.name,
            "category": cats.get(part.category_id, ""), "qty": item.qty, "unit_price": item.unit_price, "amount": item.amount,
            "extra": item.allocated_extra, "cost": money(item.amount + item.allocated_extra),
        })
    cols = [Column("date", "Дата", "date", 11), Column("number", "Закупка", width=12), Column("supplier", "Поставщик", width=26),
            Column("supplier_doc", "Документ поставщика", width=16), Column("article", "Артикул", width=16),
            Column("name", "Наименование", width=34), Column("category", "Категория", width=18), Column("qty", "Кол-во", "qty", 9),
            Column("unit_price", "Цена", "money"), Column("amount", "Сумма", "money"), Column("extra", "Доп. затраты", "money"),
            Column("cost", "Себестоимость", "money")]
    return ReportTable("purchases", "Закупки за период", cols, rows,
                       {"amount": _sum(rows, "amount"), "extra": _sum(rows, "extra"), "cost": _sum(rows, "cost")})


# --------------------------------------------------------------------------- 2. продажи


def report_sales(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    stmt = (
        select(SaleItem, Sale)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.status == DocStatus.POSTED, Sale.doc_date >= d1, Sale.doc_date <= d2)
        .order_by(Sale.doc_date, Sale.id, SaleItem.line_no)
    )
    rows = []
    unknown = 0
    for item, sale in s.execute(stmt).all():
        part = item.part
        if not _part_filter_ok(s, part, f):
            continue
        gp = money(item.amount_net - item.cogs) if item.cogs_known and item.cogs is not None else None
        if gp is None:
            unknown += 1
        rows.append({
            "date": sale.doc_date, "number": sale.number,
            "customer": sale.customer.name if sale.customer else (sale.customer_name or "Розничный покупатель"),
            "article": part.oem_number, "name": part.name, "qty": item.qty, "markup": item.markup_pct, "unit_price": item.unit_price,
            "gross": item.amount_gross, "discount": item.discount_amount, "net": item.amount_net,
            "cogs": item.cogs if item.cogs_known else None, "gp": gp, "returned_qty": item.returned_qty,
        })
    cols = [Column("date", "Дата", "date", 11), Column("number", "Продажа", width=12), Column("customer", "Покупатель", width=24),
            Column("article", "Артикул", width=16), Column("name", "Наименование", width=32), Column("qty", "Кол-во", "qty", 8),
            Column("markup", "Наценка", "percent", 9), Column("unit_price", "Цена", "money"), Column("gross", "Сумма", "money"),
            Column("discount", "Скидка", "money"), Column("net", "К оплате", "money"), Column("cogs", "Себестоимость", "money"),
            Column("gp", "Валовая прибыль", "money"), Column("returned_qty", "Возвращено", "qty", 10)]
    notes = []
    if unknown:
        notes.append(f"Строк без известной себестоимости: {unknown}. Прибыль по ним не рассчитывается.")
    notes.append("Возвраты отражены в отчёте «Возвраты и корректировки» и уменьшают выручку в сводном отчёте.")
    return ReportTable("sales", "Продажи за период", cols, rows,
                       {k: _sum(rows, k) for k in ("gross", "discount", "net", "cogs", "gp")}, notes)


# --------------------------------------------------------------------------- 3. остатки


def report_stock(s: Session, f: ReportFilters) -> ReportTable:
    summary = inventory.stock_summary(s)
    cats = _cats(s)
    rows = []
    for part in s.execute(select(Part).where(Part.is_active.is_(True)).order_by(Part.name)).scalars():
        if not _part_filter_ok(s, part, f):
            continue
        st = summary.get(part.id, {"on_hand": ZERO, "reserved": ZERO, "available": ZERO, "value": ZERO, "unknown_qty": ZERO})
        rows.append({
            "code": part.code, "article": part.oem_number, "name": part.name, "brand": part.brand,
            "category": cats.get(part.category_id, ""), "location": part.location, "unit": part.unit,
            "on_hand": st["on_hand"], "reserved": st["reserved"], "available": st["available"], "min_stock": part.min_stock,
            "value": money(st["value"]), "unknown_qty": st["unknown_qty"],
            "low": "да" if part.min_stock > 0 and st["available"] < part.min_stock else "",
        })
    cols = [Column("code", "Код", width=10), Column("article", "Артикул", width=16), Column("name", "Наименование", width=34),
            Column("brand", "Бренд", width=12), Column("category", "Категория", width=18), Column("location", "Место", width=10),
            Column("unit", "Ед.", width=6), Column("on_hand", "Остаток", "qty", 9), Column("reserved", "Резерв", "qty", 9),
            Column("available", "Доступно", "qty", 9), Column("min_stock", "Мин.", "qty", 8),
            Column("value", "Себестоимость остатка", "money", 16), Column("unknown_qty", "Без себестоимости", "qty", 12),
            Column("low", "Заканчивается", width=12)]
    notes = []
    unknown = sum(1 for r in rows if r["unknown_qty"] > 0)
    if unknown:
        notes.append(f"Позиций с остатком без себестоимости: {unknown} (начальные остатки/излишки без цены).")
    return ReportTable("stock", "Складские остатки", cols, rows, {"value": _sum(rows, "value")}, notes)


# --------------------------------------------------------------------------- 4. движение


def report_movements(s: Session, f: ReportFilters) -> ReportTable:
    a, b = _in_period_dt(StockMovement.ts, f)
    rows = []
    for m in s.execute(select(StockMovement).where(a, b).order_by(StockMovement.ts, StockMovement.id)).scalars():
        part = s.get(Part, m.part_id)
        if not _part_filter_ok(s, part, f):
            continue
        rows.append({"ts": m.ts, "kind": MovementKind.TITLES.get(m.kind, m.kind), "doc": f"{m.doc_type} № {m.doc_id}",
                     "article": part.oem_number, "name": part.name, "qty": m.qty, "cost": m.cost, "note": m.note})
    cols = [Column("ts", "Дата и время", "datetime", 17), Column("kind", "Операция", width=26), Column("doc", "Документ", width=18),
            Column("article", "Артикул", width=16), Column("name", "Наименование", width=34), Column("qty", "Количество", "qty", 10),
            Column("cost", "Себестоимость", "money"), Column("note", "Примечание", width=24)]
    return ReportTable("movements", "Движение товаров", cols, rows, {"qty": None})


# --------------------------------------------------------------------------- 5. себестоимость реализованного


def _closed_wos(s: Session, f: ReportFilters):
    a, b = _in_period_dt(WorkOrder.closed_at, f)
    stmt = select(WorkOrder).where(WorkOrder.status == WOStatus.ISSUED, a, b).order_by(WorkOrder.closed_at)
    if f.work_order_number:
        stmt = stmt.where(WorkOrder.number == f.work_order_number.strip())
    return list(s.execute(stmt).scalars())


def report_cogs(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    rows = []
    for item, sale in s.execute(
        select(SaleItem, Sale).join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.status == DocStatus.POSTED, Sale.doc_date >= d1, Sale.doc_date <= d2)
    ).all():
        if _part_filter_ok(s, item.part, f):
            rows.append({"date": sale.doc_date, "doc": f"Продажа {sale.number}", "article": item.part.oem_number,
                         "name": item.part.name, "qty": item.qty, "cogs": item.cogs if item.cogs_known else None})
    for wo in _closed_wos(s, f):
        for p in wo.parts:
            if p.qty > 0 and _part_filter_ok(s, p.part, f):
                rows.append({"date": wo.closed_at.date(), "doc": f"Заказ-наряд № {wo.number}", "article": p.part.oem_number,
                             "name": p.part.name, "qty": p.qty, "cogs": p.cogs if p.cogs_known else None})
    for ri, ret in s.execute(
        select(SaleReturnItem, SaleReturn).join(SaleReturn, SaleReturn.id == SaleReturnItem.return_id)
        .where(SaleReturn.doc_date >= d1, SaleReturn.doc_date <= d2)
    ).all():
        item = s.get(SaleItem, ri.sale_item_id)
        if _part_filter_ok(s, item.part, f):
            rows.append({"date": ret.doc_date, "doc": f"Возврат {ret.number}", "article": item.part.oem_number,
                         "name": item.part.name, "qty": -ri.qty, "cogs": -ri.cogs if ri.cogs is not None else None})
    rows.sort(key=lambda r: r["date"])
    cols = [Column("date", "Дата", "date", 11), Column("doc", "Документ", width=24), Column("article", "Артикул", width=16),
            Column("name", "Наименование", width=34), Column("qty", "Количество", "qty", 10), Column("cogs", "Себестоимость", "money")]
    notes = []
    unknown = sum(1 for r in rows if r["cogs"] is None)
    if unknown:
        notes.append(f"Строк с неизвестной себестоимостью: {unknown} — в итог не включены.")
    return ReportTable("cogs", "Себестоимость реализованных запчастей", cols, rows, {"cogs": _sum(rows, "cogs")}, notes)


# --------------------------------------------------------------------------- 6. валовая прибыль


def report_gross_profit(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    rows = []
    for sale in s.execute(select(Sale).where(Sale.status == DocStatus.POSTED, Sale.doc_date >= d1, Sale.doc_date <= d2)).scalars():
        cogs = sale.total_cogs if sale.cogs_complete else None
        rows.append({"date": sale.doc_date, "doc": f"Продажа {sale.number}", "revenue": sale.total_net, "cogs": cogs})
    for wo in _closed_wos(s, f):
        rows.append({"date": wo.closed_at.date(), "doc": f"Заказ-наряд № {wo.number}", "revenue": wo.total_amount,
                     "cogs": wo.cogs_amount if wo.cogs_complete else None})
    for ret in s.execute(select(SaleReturn).where(SaleReturn.doc_date >= d1, SaleReturn.doc_date <= d2)).scalars():
        rows.append({"date": ret.doc_date, "doc": f"Возврат {ret.number}", "revenue": -ret.total_refund,
                     "cogs": -ret.cogs_restored if ret.cogs_restored is not None else None})
    for r in rows:
        r["gp"] = money(r["revenue"] - r["cogs"]) if r["cogs"] is not None else None
        r["margin"] = (r["gp"] / r["revenue"] * 100).quantize(Decimal("0.01")) if r["gp"] is not None and r["revenue"] else None
    rows.sort(key=lambda r: r["date"])
    known = [r for r in rows if r["gp"] is not None]
    notes = ["Выручка заказ-нарядов включает работы; себестоимость работ (зарплата) учитывается в расходах."]
    if len(known) != len(rows):
        notes.append(f"Документов с неполной себестоимостью: {len(rows) - len(known)} — прибыль по ним не показана и в итог не входит.")
    cols = [Column("date", "Дата", "date", 11), Column("doc", "Документ", width=26), Column("revenue", "Выручка", "money"),
            Column("cogs", "Себестоимость", "money"), Column("gp", "Валовая прибыль", "money"), Column("margin", "Рентабельность", "percent", 12)]
    return ReportTable("gross_profit", "Валовая прибыль", cols, rows,
                       {"revenue": _sum(rows, "revenue"), "cogs": _sum(known, "cogs"), "gp": _sum(known, "gp")}, notes)


# --------------------------------------------------------------------------- 7. работы


def report_services(s: Session, f: ReportFilters) -> ReportTable:
    rows = []
    by_mech: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for wo in _closed_wos(s, f):
        for x in wo.services:
            if not x.is_done:
                continue
            mech = x.employee.full_name if x.employee else "не указан"
            rows.append({"date": wo.closed_at.date(), "wo": wo.number, "vehicle": f"{wo.vehicle.make} {wo.vehicle.model}".strip(),
                         "plate": wo.vehicle.plate, "name": x.name, "mechanic": mech, "qty": x.qty, "gross": x.amount_gross,
                         "discount": x.discount_amount, "net": x.amount_net})
            by_mech[mech] += x.amount_net
    cols = [Column("date", "Дата выдачи", "date", 11), Column("wo", "Заказ-наряд", width=11), Column("vehicle", "Автомобиль", width=22),
            Column("plate", "Госномер", width=11), Column("name", "Работа", width=34), Column("mechanic", "Механик", width=20),
            Column("qty", "Кол-во/н-ч", "qty", 9), Column("gross", "Стоимость", "money"), Column("discount", "Скидка", "money"),
            Column("net", "Итого", "money")]
    notes = [f"{m}: {money(v)} ₽" for m, v in sorted(by_mech.items())]
    return ReportTable("services", "Стоимость выполненных работ", cols, rows,
                       {k: _sum(rows, k) for k in ("gross", "discount", "net")}, notes)


# --------------------------------------------------------------------------- 8. доходы по заказ-нарядам


def report_work_orders(s: Session, f: ReportFilters) -> ReportTable:
    from autoservice.application.payments import net_paid_work_order

    rows = []
    for wo in _closed_wos(s, f):
        paid = net_paid_work_order(s, wo.id)
        rows.append({"date": wo.closed_at.date(), "number": wo.number, "vehicle": f"{wo.vehicle.make} {wo.vehicle.model}".strip(),
                     "plate": wo.vehicle.plate, "customer": wo.customer.name if wo.customer else "", "works": wo.services_amount,
                     "parts": wo.parts_amount, "discount": wo.discount_amount, "total": wo.total_amount,
                     "cogs": wo.cogs_amount if wo.cogs_complete else None,
                     "gp": money(wo.total_amount - wo.cogs_amount) if wo.cogs_complete else None,
                     "paid": paid, "debt": money(wo.total_amount - paid)})
    cols = [Column("date", "Дата выдачи", "date", 11), Column("number", "№", width=9), Column("vehicle", "Автомобиль", width=22),
            Column("plate", "Госномер", width=11), Column("customer", "Клиент", width=24), Column("works", "Работы", "money"),
            Column("parts", "Запчасти", "money"), Column("discount", "Скидка", "money"), Column("total", "Итого", "money"),
            Column("cogs", "Себестоимость", "money"), Column("gp", "Валовая прибыль", "money"), Column("paid", "Оплачено", "money"),
            Column("debt", "Долг", "money")]
    return ReportTable("work_orders", "Доходы по заказ-нарядам", cols, rows,
                       {k: _sum(rows, k) for k in ("works", "parts", "discount", "total", "cogs", "gp", "paid", "debt")})


# --------------------------------------------------------------------------- 9. деньги


def report_cash(s: Session, f: ReportFilters) -> ReportTable:
    from autoservice.application.payments import payment_dict

    d1, d2 = f.period()
    rows = []
    by_method: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in s.execute(select(Payment).where(Payment.status == "posted", Payment.doc_date >= d1, Payment.doc_date <= d2)
                       .order_by(Payment.doc_date, Payment.id)).scalars():
        d = payment_dict(s, p)
        rows.append({"date": p.doc_date, "number": p.number, "kind": d["kind_title"], "method": d["method_title"],
                     "document": d["document"], "party": d["party"], "inflow": p.amount if p.direction == "in" else None,
                     "outflow": p.amount if p.direction == "out" else None})
        by_method[d["method_title"]] += p.amount if p.direction == "in" else -p.amount
    cols = [Column("date", "Дата", "date", 11), Column("number", "Платёж", width=12), Column("kind", "Вид", width=24),
            Column("method", "Способ", width=18), Column("document", "Документ", width=24), Column("party", "Контрагент", width=24),
            Column("inflow", "Поступление", "money"), Column("outflow", "Выплата", "money")]
    notes = [f"{k}: {money(v)} ₽ (сальдо)" for k, v in sorted(by_method.items())]
    notes.append("Операционные расходы — в отчёте «Расходы»; в сводном отчёте учитываются в движении денег.")
    return ReportTable("cash", "Фактические поступления и выплаты денег", cols, rows,
                       {"inflow": _sum(rows, "inflow"), "outflow": _sum(rows, "outflow")}, notes)


# --------------------------------------------------------------------------- 10. расходы


def report_expenses(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    rows = []
    by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in s.execute(select(Expense).where(Expense.status == "posted", Expense.doc_date >= d1, Expense.doc_date <= d2)
                       .order_by(Expense.doc_date, Expense.id)).scalars():
        rows.append({"date": e.doc_date, "number": e.number, "category": e.category, "payee": e.payee,
                     "method": PaymentMethod.TITLES.get(e.method, e.method), "description": e.description, "amount": e.amount})
        by_cat[e.category] += e.amount
    cols = [Column("date", "Дата", "date", 11), Column("number", "Номер", width=12), Column("category", "Статья", width=22),
            Column("payee", "Получатель", width=24), Column("method", "Способ", width=18), Column("description", "Описание", width=30),
            Column("amount", "Сумма", "money")]
    return ReportTable("expenses", "Расходы", cols, rows, {"amount": _sum(rows, "amount")},
                       [f"{k}: {money(v)} ₽" for k, v in sorted(by_cat.items())])


# --------------------------------------------------------------------------- 11–12. задолженности


def report_customer_debts(s: Session, f: ReportFilters) -> ReportTable:
    from autoservice.application.payments import net_paid_sale, net_paid_work_order
    from autoservice.application.sales import returns_total

    today = date.today()
    rows = []
    for wo in s.execute(select(WorkOrder).where(WorkOrder.status == WOStatus.ISSUED)).scalars():
        paid = net_paid_work_order(s, wo.id)
        debt = money(wo.total_amount - paid)
        if debt > 0:
            rows.append({"date": wo.closed_at.date(), "doc": f"Заказ-наряд № {wo.number}", "customer": wo.customer.name if wo.customer else "",
                         "phone": wo.customer.phone if wo.customer else "", "total": wo.total_amount, "paid": paid, "debt": debt,
                         "days": (today - wo.closed_at.date()).days})
    for sale in s.execute(select(Sale).where(Sale.status == DocStatus.POSTED)).scalars():
        paid = net_paid_sale(s, sale.id)
        due = money(sale.total_net - returns_total(s, sale.id))
        if due - paid > 0:
            rows.append({"date": sale.doc_date, "doc": f"Продажа {sale.number}",
                         "customer": sale.customer.name if sale.customer else (sale.customer_name or "Розничный покупатель"),
                         "phone": sale.customer.phone if sale.customer else "", "total": due, "paid": paid, "debt": money(due - paid),
                         "days": (today - sale.doc_date).days})
    rows.sort(key=lambda r: r["date"])
    cols = [Column("date", "Дата", "date", 11), Column("doc", "Документ", width=24), Column("customer", "Клиент", width=26),
            Column("phone", "Телефон", width=16), Column("total", "Сумма", "money"), Column("paid", "Оплачено", "money"),
            Column("debt", "Долг", "money"), Column("days", "Дней", "int", 7)]
    return ReportTable("customer_debts", "Задолженности клиентов (на текущую дату)", cols, rows,
                       {k: _sum(rows, k) for k in ("total", "paid", "debt")})


def report_supplier_debts(s: Session, f: ReportFilters) -> ReportTable:
    from autoservice.application.purchasing import outstanding, paid_amount, returns_credit

    rows = []
    stmt = select(PurchaseOrder).where(PurchaseOrder.status == DocStatus.POSTED).order_by(PurchaseOrder.doc_date)
    if f.supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == f.supplier_id)
    for po in s.execute(stmt).scalars():
        out = outstanding(s, po)
        if out > 0:
            rows.append({"date": po.doc_date, "number": po.number, "supplier": po.supplier.name if po.supplier else "",
                         "supplier_doc": po.supplier_doc_number, "total": po.total, "returns": returns_credit(s, po.id),
                         "paid": paid_amount(s, po.id), "debt": out})
    cols = [Column("date", "Дата", "date", 11), Column("number", "Закупка", width=12), Column("supplier", "Поставщик", width=28),
            Column("supplier_doc", "Документ поставщика", width=16), Column("total", "Сумма", "money"),
            Column("returns", "Возвраты", "money"), Column("paid", "Оплачено", "money"), Column("debt", "Долг", "money")]
    return ReportTable("supplier_debts", "Задолженности перед поставщиками (на текущую дату)", cols, rows,
                       {k: _sum(rows, k) for k in ("total", "returns", "paid", "debt")})


# --------------------------------------------------------------------------- 13. возвраты и корректировки


def report_corrections(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    rows = []
    for r in s.execute(select(SaleReturn).where(SaleReturn.doc_date >= d1, SaleReturn.doc_date <= d2)).scalars():
        rows.append({"date": r.doc_date, "type": "Возврат от покупателя", "doc": r.number,
                     "description": f"По продаже {s.get(Sale, r.sale_id).number}", "amount": -r.total_refund, "reason": r.reason})
    for r in s.execute(select(SupplierReturn).where(SupplierReturn.doc_date >= d1, SupplierReturn.doc_date <= d2)).scalars():
        rows.append({"date": r.doc_date, "type": "Возврат поставщику", "doc": r.number,
                     "description": f"По закупке {s.get(PurchaseOrder, r.purchase_id).number}", "amount": r.total_credit, "reason": r.reason})
    titles = {"write_off": "Списание", "surplus": "Оприходование", "opening": "Начальный остаток"}
    for adj in s.execute(select(StockAdjustment).where(StockAdjustment.doc_date >= d1, StockAdjustment.doc_date <= d2)).scalars():
        part = s.get(Part, adj.part_id)
        rows.append({"date": adj.doc_date, "type": titles[adj.kind], "doc": adj.number, "description": f"{part.name} × {adj.qty}",
                     "amount": (-adj.cost if adj.kind == "write_off" and adj.cost is not None else adj.cost), "reason": adj.reason})
    for model, title, num in ((Sale, "Отмена продажи", "number"), (PurchaseOrder, "Отмена закупки", "number"),
                              (Payment, "Отмена платежа", "number"), (Expense, "Отмена расхода", "number")):
        lo, hi = _in_period_dt(model.cancelled_at, f)
        for obj in s.execute(select(model).where(model.cancelled_at.is_not(None), lo, hi)).scalars():
            amount = getattr(obj, "total_net", None) or getattr(obj, "total", None) or getattr(obj, "amount", None)
            rows.append({"date": obj.cancelled_at.date(), "type": title, "doc": getattr(obj, num), "description": "",
                         "amount": amount, "reason": obj.cancel_reason})
    lo, hi = _in_period_dt(WorkOrder.cancelled_at, f)
    for wo in s.execute(select(WorkOrder).where(WorkOrder.status == WOStatus.CANCELLED, lo, hi)).scalars():
        rows.append({"date": wo.cancelled_at.date(), "type": "Отмена заказ-наряда", "doc": wo.number, "description": "",
                     "amount": None, "reason": wo.cancel_reason})
    rows.sort(key=lambda r: r["date"])
    cols = [Column("date", "Дата", "date", 11), Column("type", "Операция", width=22), Column("doc", "Документ", width=12),
            Column("description", "Описание", width=34), Column("amount", "Сумма", "money"), Column("reason", "Причина", width=30)]
    return ReportTable("corrections", "Возвраты и корректировки", cols, rows, {})


# --------------------------------------------------------------------------- 14. инвентаризация


def report_inventory(s: Session, f: ReportFilters) -> ReportTable:
    d1, d2 = f.period()
    rows = []
    for item, cnt in s.execute(
        select(InventoryCountItem, InventoryCount).join(InventoryCount, InventoryCount.id == InventoryCountItem.count_id)
        .where(InventoryCount.status == "posted", InventoryCount.doc_date >= d1, InventoryCount.doc_date <= d2)
        .order_by(InventoryCount.doc_date, InventoryCount.id)
    ).all():
        part = s.get(Part, item.part_id)
        if not _part_filter_ok(s, part, f):
            continue
        rows.append({"date": cnt.doc_date, "number": cnt.number, "article": part.oem_number, "name": part.name,
                     "expected": item.qty_expected, "actual": item.qty_actual, "diff": item.qty_diff, "cost_diff": item.cost_diff,
                     "result": "Недостача" if (item.qty_diff or 0) < 0 else ("Излишек" if (item.qty_diff or 0) > 0 else "")})
    shortage = money(sum((-r["cost_diff"] for r in rows if r["cost_diff"] is not None and r["cost_diff"] < 0), ZERO))
    cols = [Column("date", "Дата", "date", 11), Column("number", "Документ", width=12), Column("article", "Артикул", width=16),
            Column("name", "Наименование", width=34), Column("expected", "Учётный", "qty", 9), Column("actual", "Фактический", "qty", 10),
            Column("diff", "Разница", "qty", 9), Column("cost_diff", "Сумма разницы", "money"), Column("result", "Результат", width=12)]
    return ReportTable("inventory", "Инвентаризация (недостачи и излишки)", cols, rows, {"cost_diff": _sum(rows, "cost_diff")},
                       [f"Сумма недостач по себестоимости: {shortage} ₽"])


# --------------------------------------------------------------------------- 15. сводный


def financial_summary(s: Session, f: ReportFilters) -> dict:
    d1, d2 = f.period()
    sales_rows = s.execute(select(Sale).where(Sale.status == DocStatus.POSTED, Sale.doc_date >= d1, Sale.doc_date <= d2)).scalars().all()
    wos = _closed_wos(s, ReportFilters(d1, d2))
    rets = s.execute(select(SaleReturn).where(SaleReturn.doc_date >= d1, SaleReturn.doc_date <= d2)).scalars().all()
    exps = s.execute(select(Expense).where(Expense.status == "posted", Expense.doc_date >= d1, Expense.doc_date <= d2)).scalars().all()
    pays = s.execute(select(Payment).where(Payment.status == "posted", Payment.doc_date >= d1, Payment.doc_date <= d2)).scalars().all()

    def tot(items, attr):
        return money(sum((getattr(x, attr) for x in items if getattr(x, attr) is not None), ZERO))

    wo_services_net = money(sum((sum((x.amount_net for x in wo.services if x.is_done), ZERO) for wo in wos), ZERO))
    wo_parts_net = money(sum((wo.total_amount for wo in wos), ZERO) - wo_services_net)
    revenue_sales = tot(sales_rows, "total_net")
    returns = tot(rets, "total_refund")
    net_revenue = money(revenue_sales + wo_services_net + wo_parts_net - returns)
    discounts = money(tot(sales_rows, "total_discount") + tot(wos, "discount_amount"))
    cogs_sales = tot(sales_rows, "total_cogs")
    cogs_wo = tot(wos, "cogs_amount")
    cogs_ret = tot(rets, "cogs_restored")
    cogs = money(cogs_sales + cogs_wo - cogs_ret)
    incomplete = sum(1 for x in sales_rows if not x.cogs_complete) + sum(1 for x in wos if not x.cogs_complete) + sum(
        1 for r in rets if r.cogs_restored is None)
    opex = tot(exps, "amount")
    by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in exps:
        by_cat[e.category] += e.amount
    cash_in = money(sum((p.amount for p in pays if p.direction == "in"), ZERO))
    cash_out_payments = money(sum((p.amount for p in pays if p.direction == "out"), ZERO))
    cash_in_by_method: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in pays:
        if p.direction == "in" and p.kind == PaymentKind.CUSTOMER_PAYMENT:
            cash_in_by_method[PaymentMethod.TITLES[p.method]] += p.amount
    cust_debts = report_customer_debts(s, f).totals.get("debt", ZERO)
    supp_debts = report_supplier_debts(s, ReportFilters()).totals.get("debt", ZERO)
    stock_value = money(sum((v["value"] for v in inventory.stock_summary(s).values()), ZERO))
    settings = get_all(s)
    return {
        "period": (d1, d2),
        "revenue_sales": revenue_sales, "revenue_works": wo_services_net, "revenue_wo_parts": wo_parts_net, "returns": returns,
        "net_revenue": net_revenue, "discounts": discounts, "cogs_sales": cogs_sales, "cogs_wo": cogs_wo, "cogs_returns": cogs_ret,
        "cogs": cogs, "cogs_incomplete_docs": incomplete, "gross_profit": money(net_revenue - cogs), "opex": opex,
        "opex_by_category": dict(by_cat), "operating_result": money(net_revenue - cogs - opex), "cash_in": cash_in,
        "cash_in_by_method": dict(cash_in_by_method), "cash_out_payments": cash_out_payments, "cash_out_expenses": opex,
        "net_cash_flow": money(cash_in - cash_out_payments - opex), "customer_debts": cust_debts, "supplier_debts": supp_debts,
        "stock_value": stock_value, "sales_count": len(sales_rows), "work_orders_closed": len(wos),
        "tax_regime": settings.get("tax.regime") or "", "vat_mode": settings.get("tax.vat_mode") or "not_configured",
        "vat_rate": settings.get("tax.vat_rate"),
    }


def report_summary(s: Session, f: ReportFilters) -> ReportTable:
    x = financial_summary(s, f)
    rows = []

    def add(section, name, value, note=""):
        rows.append({"section": section, "name": name, "value": value, "note": note})

    add("Выручка", "Продажи запчастей (за вычетом скидок)", x["revenue_sales"])
    add("Выручка", "Работы по заказ-нарядам", x["revenue_works"])
    add("Выручка", "Запчасти в заказ-нарядах", x["revenue_wo_parts"])
    add("Выручка", "Возвраты покупателям", -x["returns"])
    add("Выручка", "Выручка (нетто)", x["net_revenue"], "итог")
    add("Выручка", "Предоставлено скидок (справочно)", x["discounts"])
    add("Себестоимость", "Себестоимость проданных запчастей", x["cogs_sales"])
    add("Себестоимость", "Себестоимость запчастей в заказ-нарядах", x["cogs_wo"])
    add("Себестоимость", "Себестоимость возвращённых товаров", -x["cogs_returns"])
    add("Себестоимость", "Себестоимость итого", x["cogs"], "итог")
    add("Результат", "Валовая прибыль", x["gross_profit"], "итог")
    add("Результат", "Операционные расходы", -x["opex"])
    for cat, v in sorted(x["opex_by_category"].items()):
        add("Результат", f"  в т.ч. {cat}", -money(v))
    add("Результат", "Расчётный финансовый результат (управленческий)", x["operating_result"], "итог")
    add("Деньги", "Поступило денег", x["cash_in"])
    for m, v in sorted(x["cash_in_by_method"].items()):
        add("Деньги", f"  в т.ч. {m}", money(v))
    add("Деньги", "Выплачено (поставщикам и возвраты)", -x["cash_out_payments"])
    add("Деньги", "Оплачено расходов", -x["cash_out_expenses"])
    add("Деньги", "Чистый денежный поток", x["net_cash_flow"], "итог")
    add("Задолженности", "Долги клиентов (на сегодня)", x["customer_debts"])
    add("Задолженности", "Долги перед поставщиками (на сегодня)", x["supplier_debts"])
    add("Склад", "Себестоимость складских остатков (на сегодня)", x["stock_value"])
    notes = ["Управленческий расчёт по проведённым операциям. Не является бухгалтерской или налоговой отчётностью."]
    if x["cogs_incomplete_docs"]:
        notes.append(f"Документов с неизвестной себестоимостью: {x['cogs_incomplete_docs']} — себестоимость и прибыль занижены/неполны.")
    if not x["tax_regime"] and x["vat_mode"] == "not_configured":
        notes.append("Налоговые параметры не настроены — налоги и НДС не рассчитываются.")
    else:
        regime = x["tax_regime"] or "не указан"
        notes.append(f"Налоговый режим: {regime}; НДС: {VAT_MODES.get(x['vat_mode'], x['vat_mode'])}. Налоги программой не начисляются.")
    cols = [Column("section", "Раздел", width=16), Column("name", "Показатель", width=50), Column("value", "Сумма", "money", 16),
            Column("note", "", width=6)]
    return ReportTable("summary", "Сводный финансовый отчёт", cols, rows, {}, notes)


# --------------------------------------------------------------------------- реестр

REPORTS: dict[str, tuple[str, object, str]] = {
    "purchases": ("Закупки за период", report_purchases, Perm.REPORTS_OPERATIONAL),
    "sales": ("Продажи за период", report_sales, Perm.REPORTS_OPERATIONAL),
    "stock": ("Складские остатки", report_stock, Perm.REPORTS_OPERATIONAL),
    "movements": ("Движение товаров", report_movements, Perm.REPORTS_OPERATIONAL),
    "cogs": ("Себестоимость реализованных запчастей", report_cogs, Perm.REPORTS_FINANCE),
    "gross_profit": ("Валовая прибыль", report_gross_profit, Perm.REPORTS_FINANCE),
    "services": ("Стоимость выполненных работ", report_services, Perm.REPORTS_OPERATIONAL),
    "work_orders": ("Доходы по заказ-нарядам", report_work_orders, Perm.REPORTS_FINANCE),
    "cash": ("Фактические поступления денег", report_cash, Perm.REPORTS_FINANCE),
    "expenses": ("Расходы", report_expenses, Perm.REPORTS_FINANCE),
    "customer_debts": ("Задолженности клиентов", report_customer_debts, Perm.REPORTS_OPERATIONAL),
    "supplier_debts": ("Задолженности перед поставщиками", report_supplier_debts, Perm.REPORTS_OPERATIONAL),
    "corrections": ("Возвраты и корректировки", report_corrections, Perm.REPORTS_OPERATIONAL),
    "inventory": ("Инвентаризация", report_inventory, Perm.REPORTS_OPERATIONAL),
    "summary": ("Сводный финансовый отчёт", report_summary, Perm.REPORTS_FINANCE),
}


def available_reports(ctx: AppContext) -> list[tuple[str, str]]:
    return [(code, title) for code, (title, _, perm) in REPORTS.items() if ctx.can(perm)]


def run_report(ctx: AppContext, code: str, filters: ReportFilters | None = None) -> ReportTable:
    title, func_, perm = REPORTS[code]
    ctx.require(perm)
    f = filters or ReportFilters()
    with ctx.db.read() as s:
        table = func_(s, f)
        table.period = f.period()
        parts = []
        if f.supplier_id:
            sup = s.get(Supplier, f.supplier_id)
            parts.append(f"поставщик: {sup.name if sup else f.supplier_id}")
        if f.category_id:
            cat = s.get(PartCategory, f.category_id)
            parts.append(f"категория: {cat.name if cat else f.category_id}")
        if f.article:
            parts.append(f"артикул: {f.article}")
        if f.work_order_number:
            parts.append(f"заказ-наряд: {f.work_order_number}")
        table.filters_text = "; ".join(parts)
        return table
