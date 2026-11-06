"""Финансы: сводка, платежи, расходы, задолженности. Деньги учитываются отдельно от выручки."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from autoservice.application import finance, payments, reports
from autoservice.application.reports import ReportFilters
from autoservice.domain.enums import PaymentKind
from autoservice.security.permissions import Perm
from autoservice.ui.common import FAILED, DataTable, button, date_edit, info, label, page_header, qdate, run
from autoservice.ui.dialogs import expense_dialog, payment_dialog, reason_dialog
from autoservice.ui.pages import Page


class FinancePage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.allowed = ctx.can(Perm.FINANCE_VIEW)
        self.d1, self.d2 = date_edit(date.today().replace(day=1)), date_edit()
        self.layout_.addWidget(page_header("Финансы", "Выручка признаётся по проведённым документам, деньги — по фактическим платежам.",
                                           [label("Период:"), self.d1, self.d2, button("Показать", self.refresh)]))
        if not self.allowed:
            self.layout_.addWidget(label("Раздел доступен администратору. Оплаты клиентов принимаются в заказ-нарядах и продажах.", "Warning"))
            self.layout_.addStretch()
            return
        self.tabs = QTabWidget()
        self.summary = DataTable([("section", "Раздел", "text"), ("name", "Показатель", "text"), ("value", "Сумма", "money")])
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.addWidget(self.summary, 1)
        self.notes = label("", "Muted")
        sl.addWidget(self.notes)
        self.tabs.addTab(sw, "Сводка")
        pw = QWidget()
        pl = QVBoxLayout(pw)
        self.pays = DataTable([("number", "Платёж", "text"), ("doc_date", "Дата", "date"), ("kind_title", "Вид", "text"),
                               ("method_title", "Способ", "text"), ("signed_amount", "Сумма (+/−)", "money"), ("document", "Документ", "text"),
                               ("party", "Контрагент", "text"), ("status_title", "Статус", "text"), ("cancel_reason", "Причина отмены", "text"),
                               ("note", "Комментарий", "text")], search=True, empty_text="Платежей за период нет")
        pl.addWidget(self.pays, 1)
        row = QHBoxLayout()
        row.addWidget(button("Отменить ошибочный платёж", self.cancel_payment, danger=True))
        row.addStretch()
        pl.addLayout(row)
        self.tabs.addTab(pw, "Платежи")
        ew = QWidget()
        el = QVBoxLayout(ew)
        self.expenses = DataTable([("number", "Номер", "text"), ("doc_date", "Дата", "date"), ("category", "Статья", "text"),
                                   ("amount", "Сумма", "money"), ("method_title", "Способ", "text"), ("payee", "Получатель", "text"),
                                   ("description", "Описание", "text"), ("status_title", "Статус", "text"), ("cancel_reason", "Причина отмены", "text")],
                                  search=True, empty_text="Расходов за период нет")
        el.addWidget(self.expenses, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новый расход", self.new_expense, primary=True))
        row.addWidget(button("Отменить расход", self.cancel_expense, danger=True))
        row.addStretch()
        el.addLayout(row)
        self.tabs.addTab(ew, "Расходы")
        cw = QWidget()
        cl = QVBoxLayout(cw)
        self.cust = DataTable([("date", "Дата", "date"), ("doc", "Документ", "text"), ("customer", "Клиент", "text"), ("phone", "Телефон", "text"),
                               ("total", "Сумма", "money"), ("paid", "Оплачено", "money"), ("debt", "Долг", "money"), ("days", "Дней", "text")],
                              empty_text="Долгов клиентов нет")
        self.cust.activated.connect(self.open_debt)
        cl.addWidget(self.cust, 1)
        cl.addWidget(label("Двойной щелчок — открыть документ и принять оплату.", "Muted"))
        self.tabs.addTab(cw, "Долги клиентов")
        sw2 = QWidget()
        sl2 = QVBoxLayout(sw2)
        self.supp = DataTable([("date", "Дата", "date"), ("number", "Закупка", "text"), ("supplier", "Поставщик", "text"),
                               ("supplier_doc", "Документ", "text"), ("total", "Сумма", "money"), ("returns", "Возвраты", "money"),
                               ("paid", "Оплачено", "money"), ("debt", "Долг", "money")], empty_text="Долгов перед поставщиками нет")
        sl2.addWidget(self.supp, 1)
        row = QHBoxLayout()
        row.addWidget(button("Оплатить выбранную закупку", self.pay_supplier, primary=True))
        row.addStretch()
        sl2.addLayout(row)
        self.tabs.addTab(sw2, "Долги поставщикам")
        self.layout_.addWidget(self.tabs, 1)

    def refresh(self):
        if not self.allowed:
            return
        f = ReportFilters(qdate(self.d1), qdate(self.d2))
        s = reports.run_report(self.ctx, "summary", f)
        self.summary.set_rows([{**r, "_color": "#111827" if r["note"] else "#374151"} for r in s.rows])
        self.notes.setText("\n".join(s.notes))
        self.pays.set_rows(payments.list_payments(self.ctx, f.date_from, f.date_to))
        self.expenses.set_rows(finance.list_expenses(self.ctx, f.date_from, f.date_to))
        self.cust.set_rows(reports.run_report(self.ctx, "customer_debts").rows)
        self.supp_rows = reports.run_report(self.ctx, "supplier_debts").rows
        self.supp.set_rows(self.supp_rows)

    def cancel_payment(self):
        r = self.pays.selected()
        if not r:
            return info(self, "Выберите платёж")
        reason = reason_dialog(self, f"Отмена платежа {r['number']}", "Причина отмены")
        if reason and run(self, payments.cancel_payment, self.ctx, r["id"], reason) is not FAILED:
            self.refresh()

    def new_expense(self):
        if expense_dialog(self.ctx, self):
            self.refresh()

    def cancel_expense(self):
        r = self.expenses.selected()
        if not r:
            return info(self, "Выберите расход")
        reason = reason_dialog(self, f"Отмена расхода {r['number']}", "Причина отмены")
        if reason and run(self, finance.cancel_expense, self.ctx, r["id"], reason) is not FAILED:
            self.refresh()

    def open_debt(self, row):
        doc = row["doc"]
        if doc.startswith("Заказ-наряд"):
            from autoservice.application.work_orders import list_work_orders

            number = doc.split("№")[-1].strip()
            wo = next((w for w in list_work_orders(self.ctx, query=number) if w["number"] == number), None)
            if wo:
                self.win.go("repairs", "open", wo_id=wo["id"])
        else:
            from autoservice.application.sales import list_sales
            from autoservice.ui.pages.sales import SaleEditor

            number = doc.split(" ", 1)[-1].strip()
            sale = next((s for s in list_sales(self.ctx, query=number) if s["number"] == number), None)
            if sale:
                SaleEditor(self.ctx, sale["id"], self).exec()
        self.refresh()

    def pay_supplier(self):
        r = self.supp.selected()
        if not r:
            return info(self, "Выберите закупку")
        from autoservice.application.purchasing import list_purchases

        po = next((p for p in list_purchases(self.ctx, query=r["number"]) if p["number"] == r["number"]), None)
        if po and payment_dialog(self.ctx, self, kind=PaymentKind.SUPPLIER_PAYMENT, purchase_id=po["id"], suggested=r["debt"]):
            self.refresh()
