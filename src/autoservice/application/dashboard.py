"""Показатели главной панели. Только реальные данные: при пустой базе — нули."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from autoservice.application import inventory
from autoservice.application.context import AppContext
from autoservice.application.reports import ReportFilters, financial_summary, report_customer_debts, report_supplier_debts
from autoservice.domain.enums import WOStatus
from autoservice.domain.money import ZERO, money
from autoservice.persistence.models import InvoiceImport, Part, WorkOrder
from autoservice.security.permissions import Perm


def dashboard(ctx: AppContext, date_from: date | None = None, date_to: date | None = None) -> dict:
    today = date.today()
    period = ReportFilters(date_from or date(today.year, today.month, 1), date_to or today)
    finance = ctx.can(Perm.FINANCE_VIEW)
    with ctx.db.read() as s:
        out: dict = {"finance_visible": finance, "period": period.period()}
        if finance:
            day = financial_summary(s, ReportFilters(today, today))
            per = financial_summary(s, period)
            out.update({
                "revenue_today": day["net_revenue"],
                "revenue_period": per["net_revenue"],
                "gross_profit_period": per["gross_profit"],
                "cogs_incomplete": per["cogs_incomplete_docs"],
                "expenses_period": per["opex"],
                "cash_in_period": per["cash_in"],
                "operating_result_period": per["operating_result"],
            })
        cust = report_customer_debts(s, period)
        out["customer_debts"] = cust.totals.get("debt", ZERO)
        out["unpaid_docs"] = cust.rows[:15]
        out["supplier_debts"] = report_supplier_debts(s, ReportFilters()).totals.get("debt", ZERO)
        out["cars_in_repair"] = s.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_(WOStatus.IN_SHOP))) or 0
        d1, d2 = period.period()
        out["completed_orders"] = s.scalar(
            select(func.count()).select_from(WorkOrder).where(
                WorkOrder.status == WOStatus.ISSUED,
                func.date(WorkOrder.closed_at) >= d1.isoformat(),
                func.date(WorkOrder.closed_at) <= d2.isoformat(),
            )
        ) or 0
        summary = inventory.stock_summary(s)
        out["stock_value"] = money(sum((v["value"] for v in summary.values()), ZERO))
        low = []
        for part in s.execute(select(Part).where(Part.is_active.is_(True), Part.min_stock > 0)).scalars():
            st = summary.get(part.id)
            avail = st["available"] if st else ZERO
            if avail < part.min_stock:
                low.append({"id": part.id, "name": part.name, "article": part.oem_number, "available": avail,
                            "min_stock": part.min_stock, "unit": part.unit})
        out["low_stock"] = sorted(low, key=lambda r: r["available"] - r["min_stock"])[:20]
        out["documents_to_review"] = [
            {"id": i.id, "supplier": i.supplier_name, "doc_number": i.doc_number, "created_at": i.created_at,
             "warnings": len(i.warnings or [])}
            for i in s.execute(select(InvoiceImport).where(InvoiceImport.status == "needs_review").order_by(InvoiceImport.id.desc())).scalars()
        ]
    from autoservice.ai.recommendations import purchase_recommendations

    out["purchase_recommendations"] = purchase_recommendations(ctx)[:10]
    return out
