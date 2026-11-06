"""Продажи запчастей: список, редактор, проведение с оплатой, возвраты, документы."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from autoservice.application import catalog, sales, vehicles
from autoservice.documents import generator, printing
from autoservice.domain.enums import DocStatus, PaymentKind, PaymentMethod
from autoservice.domain.errors import ValidationError
from autoservice.domain.markup import SOURCE_TITLES
from autoservice.security.permissions import Perm
from autoservice.ui.common import (
    FAILED,
    DataTable,
    FormDialog,
    MarkupSelector,
    MoneyEdit,
    QtyEdit,
    button,
    combo,
    combo_value,
    confirm,
    date_edit,
    fmt,
    info,
    label,
    page_header,
    qdate,
    run,
)
from autoservice.ui.dialogs import PartPicker, payment_dialog, reason_dialog
from autoservice.ui.pages import Page


class SalesPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Продажи", "Продажа запчастей клиентам. Списание по FIFO при проведении.",
                                           [button("Новая продажа", self.new, primary=True)]))
        top = QHBoxLayout()
        self.status = combo([("Все", None), ("Черновики", DocStatus.DRAFT), ("Проведённые", DocStatus.POSTED), ("Отменённые", DocStatus.CANCELLED)])
        self.d1, self.d2 = date_edit(date.today().replace(day=1)), date_edit()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Номер или покупатель")
        for x in (label("Статус:"), self.status, label("Период:"), self.d1, self.d2, self.q, button("Показать", self.refresh)):
            top.addWidget(x)
        self.layout_.addLayout(top)
        self.table = DataTable([("number", "Номер", "text"), ("doc_date", "Дата", "date"), ("customer", "Покупатель", "text"),
                                ("status_title", "Статус", "text"), ("total_net", "Сумма", "money"), ("returned", "Возвращено", "money"),
                                ("paid", "Оплачено", "money"), ("debt", "Долг", "money"), ("gross_profit", "Валовая прибыль", "money")],
                               empty_text="Продаж нет")
        self.table.activated.connect(lambda r: self.open(r["id"]))
        self.layout_.addWidget(self.table, 1)

    def refresh(self):
        rows = sales.list_sales(self.ctx, qdate(self.d1), qdate(self.d2), self.status.currentData(), self.q.text())
        if not self.ctx.can(Perm.FINANCE_VIEW):
            for r in rows:
                r["gross_profit"] = None
        self.table.set_rows(rows)

    def new(self, part_id=None):
        sid = run(self, sales.create_sale, self.ctx)
        if sid is FAILED:
            return
        SaleEditor(self.ctx, sid, self, add_part=part_id).exec()
        self.refresh()

    def open(self, sale_id):
        SaleEditor(self.ctx, sale_id, self).exec()
        self.refresh()

    def handle(self, action, **kw):
        if action == "new":
            self.new(kw.get("part_id"))


class SaleLineDialog(FormDialog):
    """Строка продажи с живым расчётом: себестоимость → наценка → цена → прибыль."""

    def __init__(self, ctx, part, parent=None, qty="1", markup=None, discount=None):
        super().__init__("Строка продажи", parent, ok_text="Добавить")
        self.ctx = ctx
        self.part = part
        self.add("Запчасть", label(f"{part['name']} ({part.get('oem_number') or part.get('code')}) · доступно {fmt(part.get('available'), 'qty')} {part.get('unit', '')}"))
        self.qty = self.add("Количество*", QtyEdit(qty))
        self.markup = self.add("Наценка", MarkupSelector(auto_text="По правилам (партия / запчасть / стандартная)"))
        self.markup.set_value(markup)
        self.manual = self.add("Цена вручную", MoneyEdit(placeholder="только если себестоимость неизвестна"))
        self.discount = self.add("Скидка, %", QLineEdit(discount or ""))
        self.preview = self.add("Расчёт", label("", "Muted"))
        for sig in (self.qty.textChanged, self.manual.textChanged):
            sig.connect(lambda *_: self.recalc())
        self.markup.changed.connect(self.recalc)
        self.recalc()

    def recalc(self):
        try:
            p = sales.preview_line(self.ctx, self.part["id"], self.qty.value() or "0", self.markup.value(), self.manual.value())
        except Exception as exc:  # noqa: BLE001
            self.preview.setText(str(exc))
            return
        if p["cost_basis_total"] is None:
            text = f"Себестоимость неизвестна — цена введена вручную: {fmt(p['unit_price'], 'money')}. Прибыль не рассчитывается."
        else:
            text = (f"Себестоимость ед.: {fmt(p['unit_cost'], 'money')}{' (оценка по последней закупке)' if p['cost_estimated'] else ''} · "
                    f"наценка {fmt(p['markup_pct'], 'percent')} ({SOURCE_TITLES.get(p['markup_source'], p['markup_source'])}) · цена {fmt(p['unit_price'], 'money')} · "
                    f"сумма {fmt(p['amount_gross'], 'money')} · валовая прибыль {fmt(p['gross_profit'], 'money')}")
        if not p["enough_stock"]:
            text += f"\n⚠ Недостаточно доступного остатка ({fmt(p['available'], 'qty')})."
        self.preview.setText(text)


class SaleEditor(QDialog):
    def __init__(self, ctx, sale_id, parent=None, add_part=None):
        super().__init__(parent)
        self.ctx = ctx
        self.sid = sale_id
        self.resize(1080, 640)
        lay = QVBoxLayout(self)
        self.title = label("")
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        self.head = label("")
        lay.addWidget(self.head)
        cols = [("line_no", "№", "text"), ("part_name", "Наименование", "text"), ("oem_number", "Артикул", "text"), ("qty", "Кол-во", "qty"),
                ("unit", "Ед.", "text"), ("markup_pct", "Наценка", "percent"), ("unit_price", "Цена", "money"),
                ("discount_amount", "Скидка", "money"), ("amount_net", "Сумма", "money"), ("returned_qty", "Возвращено", "qty")]
        if ctx.can(Perm.FINANCE_VIEW):
            cols.insert(5, ("cost_basis_total", "Себестоимость", "money"))
        self.lines = DataTable(cols, empty_text="Добавьте запчасти")
        lay.addWidget(self.lines, 1)
        self.totals = label("")
        lay.addWidget(self.totals)
        row = QHBoxLayout()
        self.b_customer = button("Покупатель", self.choose_customer)
        self.b_add = button("Добавить запчасть", self.add_line, primary=True)
        self.b_edit = button("Изменить строку", self.edit_line)
        self.b_del = button("Удалить строку", self.remove_line, danger=True)
        self.b_disc = button("Скидка на всё, %", self.discount_all)
        self.b_post = button("Провести продажу", self.post, primary=True)
        self.b_delete = button("Удалить черновик", self.delete_draft, danger=True)
        self.b_pay = button("Принять оплату", self.pay)
        self.b_return = button("Возврат от покупателя", self.make_return)
        self.b_cancel = button("Отменить продажу", self.cancel, danger=True)
        self.b_doc = button("Документ продажи", self.print_doc)
        self.b_receipt = button("Квитанция (чек)", self.print_receipt)
        for b in (self.b_customer, self.b_add, self.b_edit, self.b_del, self.b_disc, self.b_post, self.b_delete, self.b_pay, self.b_return,
                  self.b_cancel, self.b_doc, self.b_receipt):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.load()
        if add_part:
            self.add_line(catalog.get_part(ctx, add_part))

    def load(self):
        s = sales.get_sale(self.ctx, self.sid)
        self.sale = s
        self.setWindowTitle(f"Продажа {s['number']}")
        self.title.setText(f"Продажа {s['number']} — {s['status_title']}")
        self.head.setText(f"Дата: {fmt(s['doc_date'], 'date')} · Покупатель: {s['customer']}"
                          + (f" · Причина отмены: {s['cancel_reason']}" if s["cancel_reason"] else ""))
        self.lines.set_rows(s["items"])
        gp = f" · Валовая прибыль: {fmt(s['gross_profit'], 'money')}" if self.ctx.can(Perm.FINANCE_VIEW) and s["status"] == "posted" else ""
        self.totals.setText(f"Сумма: {fmt(s['total_gross'], 'money')} · Скидка: {fmt(s['total_discount'], 'money')} · "
                            f"К оплате: {fmt(s['total_net'], 'money')} · Возвращено: {fmt(s['returned'], 'money')} · "
                            f"Оплачено: {fmt(s['paid'], 'money')} · Долг: {fmt(s['debt'], 'money')}{gp}")
        draft, posted = s["status"] == DocStatus.DRAFT, s["status"] == DocStatus.POSTED
        for b in (self.b_customer, self.b_add, self.b_edit, self.b_del, self.b_disc, self.b_post, self.b_delete):
            b.setVisible(draft)
        for b in (self.b_pay, self.b_return, self.b_doc, self.b_receipt):
            b.setVisible(posted)
        self.b_cancel.setVisible(posted and self.ctx.can(Perm.OPERATION_CANCEL))

    def choose_customer(self):
        customers = vehicles.search_customers(self.ctx, limit=5000)
        d = FormDialog("Покупатель", self)
        c = d.add("Клиент из базы", combo([("— розничный покупатель —", None)] + [(f"{x['name']} {x['phone']}".strip(), x["id"]) for x in customers],
                                          self.sale["customer_id"], editable=True))
        name = d.add("Или имя покупателя", QLineEdit(""))
        d.on_accept = lambda: sales.update_sale_header(self.ctx, self.sid, customer_id=combo_value(c), customer_name=name.text())
        if d.exec():
            self.load()

    def add_line(self, part=None):
        part = part or PartPicker.pick(self.ctx, self, "Запчасть для продажи", in_stock_only=True)
        if not part:
            return
        d = SaleLineDialog(self.ctx, part, self)
        d.on_accept = lambda: sales.add_sale_line(self.ctx, self.sid, part["id"], d.qty.value(), d.markup.value(), d.manual.value(),
                                                  discount_percent=d.discount.text().replace(",", ".") or None)
        if d.exec():
            self.load()

    def edit_line(self):
        row = self.lines.selected()
        if not row:
            return info(self, "Выберите строку")
        part = catalog.get_part(self.ctx, row["part_id"])
        d = SaleLineDialog(self.ctx, part, self, fmt(row["qty"], "qty"), row["markup_pct"] if row["markup_source"] == "operation" else None)
        d.buttons.button(d.buttons.StandardButton.Ok).setText("Сохранить")
        d.on_accept = lambda: sales.update_sale_line(self.ctx, row["id"], d.qty.value(), d.markup.value(), d.manual.value(),
                                                     discount_percent=d.discount.text().replace(",", ".") or None)
        if d.exec():
            self.load()

    def remove_line(self):
        row = self.lines.selected()
        if row and run(self, sales.remove_sale_line, self.ctx, row["id"]) is not FAILED:
            self.load()

    def discount_all(self):
        d = FormDialog("Скидка на все строки", self)
        p = d.add("Скидка, %", QLineEdit("0"))
        d.on_accept = lambda: sales.set_sale_discount(self.ctx, self.sid, p.text().replace(",", "."))
        if d.exec():
            self.load()

    def post(self):
        s = self.sale
        if not s["items"]:
            return info(self, "Добавьте строки")
        d = FormDialog("Проведение продажи", self, ok_text="Провести")
        d.add("Сумма к оплате", label(fmt(s["total_net"], "money")))
        method = d.add("Оплата", combo([(t, k) for k, t in PaymentMethod.TITLES.items()] + [("Без оплаты сейчас (в долг)", None)]))
        amount = d.add("Получено", MoneyEdit(s["total_net"]))
        d.form.addRow("", label("Остаток проверяется с учётом резервов; себестоимость списывается по FIFO.", "Muted"))

        def do():
            if method.currentData() is None:
                sales.post_sale(self.ctx, self.sid)
            else:
                sales.post_sale(self.ctx, self.sid, method.currentData(), amount.value())

        d.on_accept = do
        if d.exec():
            self.load()

    def delete_draft(self):
        if confirm(self, "Удалить черновик продажи?") and run(self, sales.delete_draft, self.ctx, self.sid) is not FAILED:
            self.accept()

    def pay(self):
        if payment_dialog(self.ctx, self, kind=PaymentKind.CUSTOMER_PAYMENT, sale_id=self.sid, suggested=self.sale["debt"] or None):
            self.load()

    def make_return(self):
        row = self.lines.selected()
        if not row:
            return info(self, "Выберите строку для возврата")
        d = FormDialog("Возврат от покупателя", self, ok_text="Оформить возврат")
        d.add("Запчасть", label(f"{row['part_name']} · продано {fmt(row['qty'], 'qty')}, возвращено {fmt(row['returned_qty'], 'qty')}"))
        q = d.add("Количество*", QtyEdit(fmt(row["qty"] - row["returned_qty"], "qty")))
        reason = d.add("Причина*", QLineEdit())
        refund = d.add("Вернуть деньги", combo([(t, k) for k, t in PaymentMethod.TITLES.items()] + [("Не возвращать сейчас", None)]))
        d.on_accept = lambda: sales.create_return(self.ctx, self.sid, [(row["id"], q.value())], reason.text(), refund.currentData())
        if d.exec():
            self.load()

    def cancel(self):
        reason = reason_dialog(self, "Отмена продажи", "Причина отмены")
        if reason and run(self, sales.cancel_sale, self.ctx, self.sid, reason) is not FAILED:
            self.load()

    def print_doc(self):
        path = run(self, generator.sale_pdf, self.ctx, self.sid)
        if path is not FAILED:
            printing.print_with_preview(path, self)

    def print_receipt(self):
        path = run(self, generator.thermal_receipt_pdf, self.ctx, "sale", self.sid)
        if path is not FAILED:
            printing.print_with_preview(path, self)


__all__ = ["SalesPage", "SaleEditor", "SaleLineDialog", "ValidationError"]
