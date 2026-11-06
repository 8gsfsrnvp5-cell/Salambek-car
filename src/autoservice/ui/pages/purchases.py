"""Закупки: список, редактор закупки, распознавание накладных."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from autoservice.ai import invoice_import
from autoservice.application import catalog, purchasing
from autoservice.application.catalog import list_suppliers
from autoservice.application.documents_store import document_file
from autoservice.documents import generator, printing
from autoservice.domain.enums import DocStatus, PaymentKind
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
    open_path,
    page_header,
    pick_file,
    qdate,
    run,
)
from autoservice.ui.dialogs import PartDialog, PartPicker, edit_supplier, payment_dialog, reason_dialog
from autoservice.ui.pages import Page

INVOICE_FILTER = "Накладные (*.pdf *.xlsx *.csv *.txt *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)"


class PurchasesPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Закупки", "Поступление запчастей. Проведение увеличивает остаток; повторное проведение невозможно.",
                                           [button("Новая закупка", self.new, primary=True), button("Распознать накладную", self.import_file)]))
        self.tabs = QTabWidget()
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.status = combo([("Все", None), ("Черновики", DocStatus.DRAFT), ("Проведённые", DocStatus.POSTED), ("Отменённые", DocStatus.CANCELLED)])
        self.d1, self.d2 = date_edit(date(date.today().year, 1, 1)), date_edit()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Номер закупки или документа поставщика")
        for x in (label("Статус:"), self.status, label("Период:"), self.d1, self.d2, self.q, button("Показать", self.refresh)):
            top.addWidget(x)
        lay.addLayout(top)
        self.table = DataTable([("number", "Номер", "text"), ("doc_date", "Дата", "date"), ("supplier", "Поставщик", "text"),
                                ("supplier_doc_number", "Документ поставщика", "text"), ("status_title", "Статус", "text"),
                                ("total", "Сумма", "money"), ("paid", "Оплачено", "money"), ("outstanding", "Долг", "money"),
                                ("payment_status", "Оплата", "text")], empty_text="Закупок нет")
        self.table.activated.connect(lambda r: self.open(r["id"]))
        lay.addWidget(self.table, 1)
        self.tabs.addTab(w, "Закупки")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        self.imports = DataTable([("id", "№", "text"), ("created_at", "Загружен", "datetime"), ("status_title", "Статус", "text"),
                                  ("supplier_name", "Поставщик (из документа)", "text"), ("doc_number", "Номер", "text"),
                                  ("doc_date", "Дата", "date"), ("total_declared", "Итог документа", "money"),
                                  ("total_calculated", "Сумма строк", "money"), ("lines_count", "Строк", "text"), ("engine", "Источник текста", "text")],
                                 empty_text="Накладные ещё не загружались")
        self.imports.activated.connect(lambda r: self.review(r["id"]))
        l2.addWidget(self.imports, 1)
        l2.addWidget(label("Распознанные значения — черновые. Закупка создаётся черновиком только после проверки менеджером.", "Muted"))
        self.tabs.addTab(w2, "Распознанные накладные")
        self.layout_.addWidget(self.tabs, 1)

    def refresh(self):
        self.table.set_rows(purchasing.list_purchases(self.ctx, qdate(self.d1), qdate(self.d2), self.status.currentData(), query=self.q.text()))
        self.imports.set_rows(invoice_import.list_imports(self.ctx))

    def new(self, part_id=None):
        suppliers = list_suppliers(self.ctx)
        d = FormDialog("Новая закупка", self, ok_text="Создать")
        sup = d.add("Поставщик", combo([("— выберите —", None)] + [(s["name"], s["id"]) for s in suppliers], editable=True))
        d.add("", button("Новый поставщик", lambda: self._new_supplier(sup)))
        num = d.add("Номер документа поставщика", QLineEdit())
        sdate = d.add("Дата документа поставщика", date_edit())
        extra = d.add("Доставка и доп. затраты", MoneyEdit("0"))
        res = {}

        def create():
            try:
                res["id"] = purchasing.create_purchase(self.ctx, combo_value(sup), num.text(), qdate(sdate), extra_costs=extra.value() or "0")
            except purchasing.DuplicateDocument as exc:
                if confirm(self, f"{exc}\n\nЭто действительно другой документ с таким же номером?"):
                    res["id"] = purchasing.create_purchase(self.ctx, combo_value(sup), num.text(), qdate(sdate),
                                                           extra_costs=extra.value() or "0", allow_duplicate=True)
                else:
                    raise

        d.on_accept = create
        if d.exec() and res.get("id"):
            PurchaseEditor(self.ctx, res["id"], self, add_part=part_id).exec()
            self.refresh()

    def _new_supplier(self, sup_combo):
        sid = edit_supplier(self.ctx, self)
        if sid:
            s = next(x for x in list_suppliers(self.ctx) if x["id"] == sid)
            sup_combo.addItem(s["name"], sid)
            sup_combo.setCurrentIndex(sup_combo.count() - 1)

    def open(self, purchase_id):
        PurchaseEditor(self.ctx, purchase_id, self).exec()
        self.refresh()

    def import_file(self):
        path = pick_file(self, "Выберите накладную или счёт поставщика", INVOICE_FILTER)
        if not path:
            return
        imp_id = run(self, invoice_import.import_invoice, self.ctx, path)
        if imp_id is not FAILED:
            self.review(imp_id)

    def review(self, import_id):
        InvoiceReviewDialog(self.ctx, import_id, self).exec()
        self.refresh()

    def handle(self, action, **kw):
        if action == "new":
            self.new(kw.get("part_id"))
        elif action == "import":
            self.import_file()
        elif action == "review":
            self.tabs.setCurrentIndex(1)
            self.review(kw["import_id"])
        elif action == "open":
            self.open(kw["purchase_id"])


class PurchaseEditor(QDialog):
    def __init__(self, ctx, purchase_id, parent=None, add_part=None):
        super().__init__(parent)
        self.ctx = ctx
        self.pid = purchase_id
        self.resize(1100, 680)
        lay = QVBoxLayout(self)
        self.title = label("")
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        self.head = label("")
        lay.addWidget(self.head)
        self.lines = DataTable([("line_no", "№", "text"), ("part_code", "Код", "text"), ("oem_number", "Артикул", "text"),
                                ("part_name", "Наименование", "text"), ("qty", "Кол-во", "qty"), ("unit", "Ед.", "text"),
                                ("unit_price", "Цена", "money"), ("amount", "Сумма", "money"), ("allocated_extra", "Доп. затраты", "money"),
                                ("markup_pct", "Наценка партии", "percent"), ("name_on_invoice", "Название в накладной", "text")],
                               empty_text="Добавьте строки закупки")
        self.lines.activated.connect(lambda r: self.edit_line(r))
        lay.addWidget(self.lines, 1)
        self.totals = label("")
        lay.addWidget(self.totals)
        self.draft_row = QHBoxLayout()
        self.b_add = button("Добавить строку", self.add_line, primary=True)
        self.b_edit = button("Изменить строку", lambda: self.lines.selected() and self.edit_line(self.lines.selected()))
        self.b_del = button("Удалить строку", self.remove_line, danger=True)
        self.b_head = button("Реквизиты закупки", self.edit_header)
        self.b_post = button("Провести закупку", self.post, primary=True)
        self.b_delete = button("Удалить черновик", self.delete_draft, danger=True)
        self.b_pay = button("Оплатить поставщику", self.pay)
        self.b_return = button("Возврат поставщику", self.supplier_return)
        self.b_cancel = button("Отменить закупку", self.cancel, danger=True)
        self.b_print = button("Накладная внутреннего учёта", self.print_waybill)
        self.b_attach = button("Прикрепить накладную", self.attach)
        self.b_original = button("Открыть оригинал накладной", self.open_original)
        for b in (self.b_add, self.b_edit, self.b_del, self.b_head, self.b_post, self.b_delete, self.b_pay, self.b_return, self.b_cancel,
                  self.b_print, self.b_attach, self.b_original):
            self.draft_row.addWidget(b)
        self.draft_row.addStretch()
        lay.addLayout(self.draft_row)
        self.load()
        if add_part:
            part = catalog.get_part(ctx, add_part)
            self.add_line(part)

    def load(self):
        po = purchasing.get_purchase(self.ctx, self.pid)
        self.po = po
        self.setWindowTitle(f"Закупка {po['number']}")
        self.title.setText(f"Закупка {po['number']} — {po['status_title']}")
        sdoc = po["supplier_doc_number"] + (f" от {fmt(po['supplier_doc_date'], 'date')}" if po["supplier_doc_date"] else "")
        self.head.setText(f"Поставщик: {po['supplier'] or '— не выбран —'} · Документ поставщика: {sdoc or '—'} · "
                          f"Дата: {fmt(po['doc_date'], 'date')}" + (f" · Причина отмены: {po['cancel_reason']}" if po["cancel_reason"] else "")
                          + (f"\n{po['notes']}" if po["notes"] else ""))
        self.lines.set_rows(po["items"])
        self.totals.setText(f"Товары: {fmt(po['total_goods'], 'money')} · Доп. затраты: {fmt(po['extra_costs'], 'money')} · "
                            f"Итого: {fmt(po['total'], 'money')} · Оплачено: {fmt(po['paid'], 'money')} · "
                            f"Возвраты: {fmt(po['returns_credit'], 'money')} · Долг: {fmt(po['outstanding'], 'money')} ({po['payment_status']})")
        draft = po["status"] == DocStatus.DRAFT
        posted = po["status"] == DocStatus.POSTED
        for b in (self.b_add, self.b_edit, self.b_del, self.b_head, self.b_post, self.b_delete):
            b.setVisible(draft)
        for b in (self.b_pay, self.b_return):
            b.setVisible(posted)
        self.b_cancel.setVisible(posted and self.ctx.can(Perm.OPERATION_CANCEL))
        self.b_original.setVisible(bool(po["invoice_document_id"]))

    def _line_dialog(self, part, qty="1", price=None, markup=None):
        d = FormDialog("Строка закупки", self)
        d.add("Запчасть", label(f"{part['name']} ({part.get('oem_number') or part.get('code')})"))
        q = d.add("Количество*", QtyEdit(qty))
        p = d.add("Цена за единицу*", MoneyEdit(price))
        m = d.add("Наценка партии", MarkupSelector(auto_text="Не задавать (по правилам)"))
        m.set_value(markup)
        d.form.addRow("", label("Наценка партии имеет приоритет над наценкой запчасти и стандартной.", "Muted"))
        return d, q, p, m

    def add_line(self, part=None):
        part = part or PartPicker.pick(self.ctx, self, "Запчасть для закупки")
        if not part:
            return
        d, q, p, m = self._line_dialog(part)
        d.on_accept = lambda: purchasing.add_purchase_line(self.ctx, self.pid, part["id"], q.value(), p.value(), m.value())
        if d.exec():
            self.load()

    def edit_line(self, row):
        if self.po["status"] != DocStatus.DRAFT:
            return
        d, q, p, m = self._line_dialog({"name": row["part_name"], "oem_number": row["oem_number"]}, fmt(row["qty"], "qty"),
                                       row["unit_price"], row["markup_pct"])
        d.on_accept = lambda: purchasing.update_purchase_line(self.ctx, row["id"], q.value(), p.value(), m.value())
        if d.exec():
            self.load()

    def remove_line(self):
        row = self.lines.selected()
        if row and run(self, purchasing.remove_purchase_line, self.ctx, row["id"]) is not FAILED:
            self.load()

    def edit_header(self):
        suppliers = list_suppliers(self.ctx)
        d = FormDialog("Реквизиты закупки", self)
        sup = d.add("Поставщик", combo([("— выберите —", None)] + [(s["name"], s["id"]) for s in suppliers], self.po["supplier_id"], editable=True))
        num = d.add("Номер документа поставщика", QLineEdit(self.po["supplier_doc_number"]))
        sdate = d.add("Дата документа поставщика", date_edit(self.po["supplier_doc_date"]))
        ddate = d.add("Дата поступления", date_edit(self.po["doc_date"]))
        extra = d.add("Доставка и доп. затраты", MoneyEdit(self.po["extra_costs"]))
        note = d.add("Что входит в доп. затраты", QLineEdit(self.po["extra_costs_note"]))
        d.on_accept = lambda: purchasing.update_purchase_header(self.ctx, self.pid, supplier_id=combo_value(sup), supplier_doc_number=num.text(),
                                                                supplier_doc_date=qdate(sdate), doc_date=qdate(ddate),
                                                                extra_costs=extra.value() or "0", extra_costs_note=note.text())
        if d.exec():
            self.load()

    def post(self):
        po = self.po
        if not confirm(self, f"Провести закупку {po['number']} на {fmt(po['total'], 'money')}?\n"
                             f"Остатки увеличатся на {len(po['items'])} позиций. Изменить проведённую закупку нельзя."):
            return
        zero = any(i["unit_price"] == 0 for i in po["items"])
        if zero and not confirm(self, "В закупке есть строки с нулевой ценой. Это бесплатное поступление?"):
            return
        if run(self, purchasing.post_purchase, self.ctx, self.pid, allow_zero_price=zero, success="Закупка проведена") is not FAILED:
            self.load()

    def delete_draft(self):
        if confirm(self, "Удалить черновик закупки? Он не влиял на склад и финансы.") and \
                run(self, purchasing.delete_draft, self.ctx, self.pid) is not FAILED:
            self.accept()

    def pay(self):
        if payment_dialog(self.ctx, self, kind=PaymentKind.SUPPLIER_PAYMENT, purchase_id=self.pid, suggested=self.po["outstanding"]):
            self.load()

    def supplier_return(self):
        row = self.lines.selected()
        if not row:
            return info(self, "Выберите строку для возврата")
        d = FormDialog("Возврат поставщику", self, ok_text="Провести возврат")
        d.add("Запчасть", label(row["part_name"]))
        q = d.add("Количество*", QtyEdit(fmt(row["qty"], "qty")))
        reason = d.add("Причина*", QLineEdit())
        d.on_accept = lambda: purchasing.supplier_return(self.ctx, self.pid, [(row["id"], q.value())], reason.text())
        if d.exec():
            self.load()

    def cancel(self):
        reason = reason_dialog(self, "Отмена закупки", "Причина отмены")
        if reason and run(self, purchasing.cancel_purchase, self.ctx, self.pid, reason) is not FAILED:
            self.load()

    def print_waybill(self):
        path = run(self, generator.internal_waybill_purchase_pdf, self.ctx, self.pid)
        if path is not FAILED:
            printing.print_with_preview(path, self)

    def attach(self):
        path = pick_file(self, "Файл накладной", INVOICE_FILTER)
        if path and run(self, purchasing.attach_invoice, self.ctx, self.pid, path) is not FAILED:
            self.load()

    def open_original(self):
        path = run(self, document_file, self.ctx, self.po["invoice_document_id"])
        if path is not FAILED:
            open_path(path)


def _dec(value):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


class InvoiceReviewDialog(QDialog):
    def __init__(self, ctx, import_id, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.iid = import_id
        self.setWindowTitle(f"Проверка распознанной накладной № {import_id}")
        self.resize(1150, 700)
        lay = QVBoxLayout(self)
        self.head = label("")
        lay.addWidget(self.head)
        self.warn = label("", "Warning")
        lay.addWidget(self.warn)
        self.table = DataTable([("line_no", "№", "text"), ("article", "Артикул", "text"), ("name", "Наименование", "text"),
                                ("qty_d", "Кол-во", "qty"), ("unit", "Ед.", "text"), ("price_d", "Цена", "money"), ("amount_d", "Сумма", "money"),
                                ("match", "Сопоставление", "text"), ("flags_text", "Требует внимания", "text")],
                               empty_text="Строки не распознаны — введите закупку вручную по оригиналу")
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.buttons = [button("Выбрать запчасть", self.choose_part), button("Создать карточку", self.create_part),
                        button("Изменить кол-во/цену", self.edit_values), button("Пропустить / вернуть строку", self.toggle_skip),
                        button("Выбрать поставщика", self.choose_supplier)]
        for b in self.buttons:
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(button("Открыть оригинал", self.open_original))
        self.b_confirm = button("Создать черновик закупки", self.confirm, primary=True)
        self.b_reject = button("Отклонить", self.reject_import, danger=True)
        row2.addWidget(self.b_confirm)
        row2.addWidget(self.b_reject)
        row2.addStretch()
        row2.addWidget(button("Закрыть", self.reject))
        lay.addLayout(row2)
        self.load()

    def load(self):
        imp = invoice_import.get_import(self.ctx, self.iid)
        self.imp = imp
        self.head.setText(f"Статус: {imp['status_title']} · Поставщик в справочнике: {imp['supplier'] or '— не определён —'} "
                          f"(в документе: {imp['supplier_name'] or '—'}, ИНН {imp['supplier_inn'] or '—'}) · Документ № {imp['doc_number'] or '—'} "
                          f"от {fmt(imp['doc_date'], 'date')} · Итог документа: {fmt(imp['total_declared'], 'money')} · "
                          f"Сумма строк: {fmt(imp['total_calculated'], 'money')} · Текст: {imp['engine'] or '—'}")
        self.warn.setText("\n".join("⚠ " + w for w in imp["warnings"]))
        rows = []
        for i, ln in enumerate(imp["lines"]):
            match = ln.get("match_title") or {"ambiguous": "Несколько вариантов", "not_found": "Не найдено"}.get(ln.get("match_status"), "")
            if ln.get("skip"):
                match = "ПРОПУЩЕНА"
            rows.append({**ln, "_index": i, "qty_d": _dec(ln.get("qty")), "price_d": _dec(ln.get("price")), "amount_d": _dec(ln.get("amount")), "match": match, "flags_text": "; ".join(ln.get("flags", [])),
                         "_color": "#b45309" if (ln.get("flags") and not ln.get("skip")) else ("#9ca3af" if ln.get("skip") else None)})
        self.table.set_rows(rows)
        editable = imp["status"] == "needs_review"
        for b in self.buttons + [self.b_confirm, self.b_reject]:
            b.setEnabled(editable)

    def _row(self):
        r = self.table.selected()
        if not r:
            info(self, "Выберите строку")
        return r

    def choose_part(self):
        r = self._row()
        if not r:
            return
        part = PartPicker.pick(self.ctx, self, f"Запчасть для строки: {r['name']}")
        if part and run(self, invoice_import.update_line, self.ctx, self.iid, r["_index"], part_id=part["id"]) is not FAILED:
            self.load()

    def create_part(self):
        r = self._row()
        if not r:
            return
        unit = {"шт": "шт", "л": "л", "кг": "кг", "м": "м", "компл": "компл", "к-т": "компл"}.get((r.get("unit") or "").split(".")[0], "шт")
        d = PartDialog(self.ctx, None, self, {"name": r["name"], "oem_number": r["article"], "unit": unit})
        if d.exec() and d.part_id and run(self, invoice_import.update_line, self.ctx, self.iid, r["_index"], part_id=d.part_id) is not FAILED:
            self.load()

    def edit_values(self):
        r = self._row()
        if not r:
            return
        d = FormDialog("Значения строки (сверьте с оригиналом)", self)
        q = d.add("Количество", QtyEdit(r.get("qty") or ""))
        p = d.add("Цена", MoneyEdit(r.get("price")))
        d.on_accept = lambda: invoice_import.update_line(self.ctx, self.iid, r["_index"], qty=q.value(), price=p.value())
        if d.exec():
            self.load()

    def toggle_skip(self):
        r = self._row()
        if r and run(self, invoice_import.update_line, self.ctx, self.iid, r["_index"], skip=not r.get("skip")) is not FAILED:
            self.load()

    def choose_supplier(self):
        suppliers = list_suppliers(self.ctx)
        d = FormDialog("Поставщик", self)
        c = d.add("Поставщик", combo([(s["name"], s["id"]) for s in suppliers], self.imp["supplier_id"], editable=True))
        d.add("", button("Создать поставщика", lambda: self._create_supplier(c)))
        d.on_accept = lambda: invoice_import.set_supplier(self.ctx, self.iid, combo_value(c))
        if d.exec():
            self.load()

    def _create_supplier(self, c):
        sid = edit_supplier(self.ctx, self, {"name": self.imp["supplier_name"], "inn": self.imp["supplier_inn"]} if self.imp["supplier_name"] else None)
        if sid:
            c.addItem(next(s["name"] for s in list_suppliers(self.ctx) if s["id"] == sid), sid)
            c.setCurrentIndex(c.count() - 1)

    def open_original(self):
        path = run(self, document_file, self.ctx, self.imp["document_id"])
        if path is not FAILED:
            open_path(path)

    def confirm(self):
        if not confirm(self, "Создать черновик закупки из проверенных строк? Склад изменится только после проведения закупки."):
            return
        try:
            pid = invoice_import.confirm_import(self.ctx, self.iid)
        except purchasing.DuplicateDocument as exc:
            if not confirm(self, f"{exc}\n\nСоздать закупку несмотря на совпадение номера?"):
                return
            pid = run(self, invoice_import.confirm_import, self.ctx, self.iid, allow_duplicate=True)
        except Exception as exc:  # noqa: BLE001
            return info(self, str(exc), "Не выполнено")
        if pid is not FAILED:
            self.accept()
            PurchaseEditor(self.ctx, pid, self.parent()).exec()

    def reject_import(self):
        reason = reason_dialog(self, "Отклонить накладную", "Причина")
        if reason and run(self, invoice_import.reject_import, self.ctx, self.iid, reason) is not FAILED:
            self.load()


__all__ = ["PurchasesPage", "PurchaseEditor", "InvoiceReviewDialog"]
