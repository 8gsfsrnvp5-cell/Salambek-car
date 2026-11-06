"""Ремонты: заказ-наряды, работы, запчасти (резерв/выдача/возврат), статусы, оплаты, документы."""

from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from autoservice.application import catalog, vehicles, work_orders
from autoservice.application.finance import list_employees
from autoservice.documents import generator, printing
from autoservice.domain.enums import PaymentKind, WOStatus
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
    confirm,
    debounce,
    fmt,
    info,
    label,
    page_header,
    run,
    save_file,
)
from autoservice.ui.dialogs import PartPicker, VehicleDialog, payment_dialog, reason_dialog
from autoservice.ui.pages import Page
from autoservice.ui.pages.sales import SaleLineDialog
from autoservice.ui.pages.vehicles import VEHICLE_COLS

STATUS_FILTERS = [("Открытые (в работе)", "open"), ("Все", None)] + [(t, k) for k, t in WOStatus.TITLES.items()]


class RepairsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Ремонты", "Заказ-наряды: смета → работы и запчасти → «Готов» → «Выдан». Оплата учитывается отдельно.",
                                           [button("Новый заказ-наряд", self.new, primary=True)]))
        top = QHBoxLayout()
        self.status = combo(STATUS_FILTERS)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Номер, госномер, VIN, автомобиль, клиент")
        top.addWidget(label("Показать:"))
        top.addWidget(self.status)
        top.addWidget(self.q, 1)
        self.layout_.addLayout(top)
        self.table = DataTable([("number", "№", "text"), ("opened_at", "Открыт", "datetime"), ("status_title", "Статус", "text"),
                                ("vehicle", "Автомобиль", "text"), ("plate", "Госномер", "text"), ("customer", "Клиент", "text"),
                                ("complaint", "Жалоба", "text"), ("mechanic", "Механик", "text"), ("total", "Сумма", "money"),
                                ("paid", "Оплачено", "money"), ("debt", "Долг", "money")], empty_text="Заказ-нарядов нет")
        self.table.activated.connect(lambda r: self.open(r["id"]))
        self.layout_.addWidget(self.table, 1)
        self.timer = debounce(self, self.refresh)
        self.q.textChanged.connect(lambda *_: self.timer.start())
        self.status.currentIndexChanged.connect(lambda *_: self.refresh())

    def refresh(self):
        key = self.status.currentData()
        statuses = WOStatus.OPEN if key == "open" else ({key} if key else None)
        self.table.set_rows(work_orders.list_work_orders(self.ctx, statuses, self.q.text()))

    def new(self, vehicle_id=None):
        vehicle_id = vehicle_id or VehiclePicker.pick(self.ctx, self)
        if not vehicle_id:
            return
        v = vehicles.get_vehicle(self.ctx, vehicle_id)
        emps = list_employees(self.ctx)
        d = FormDialog(f"Новый заказ-наряд — {v['title']} {v['plate']}", self, ok_text="Открыть заказ-наряд")
        if v["history"]:
            d.add("Предыдущие обращения", label("; ".join(f"№ {h['number']} ({fmt(h['opened_at'], 'date')}): {h['complaint'][:60]}"
                                                         for h in v["history"][:5]), "Muted"))
        mileage = d.add("Пробег, км", QLineEdit(str(v["mileage"] or "")))
        complaint = d.add("Жалоба клиента", QPlainTextEdit())
        complaint.setFixedHeight(80)
        mech = d.add("Ответственный механик", combo([("— не назначен —", None)] + [(e["full_name"], e["id"]) for e in emps]))
        res = {}
        d.on_accept = lambda: res.setdefault("id", work_orders.create_work_order(self.ctx, vehicle_id, complaint.toPlainText(), mileage.text(),
                                                                                 mechanic_id=mech.currentData()))
        if d.exec():
            self.open(res["id"])

    def open(self, wo_id, add_part=None):
        WorkOrderEditor(self.ctx, wo_id, self, add_part).exec()
        self.refresh()

    def handle(self, action, **kw):
        if action == "new":
            self.new(kw.get("vehicle_id"))
        elif action == "open":
            self.open(kw["wo_id"], kw.get("add_part"))


class VehiclePicker(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.vehicle_id = None
        self.setWindowTitle("Выбор автомобиля")
        self.resize(900, 500)
        lay = QVBoxLayout(self)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Госномер, VIN, марка/модель, клиент или телефон")
        lay.addWidget(self.q)
        self.table = DataTable(VEHICLE_COLS, empty_text="Не найдено. Создайте новый автомобиль.")
        self.table.activated.connect(lambda r: self._done(r["id"]))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новый автомобиль", self.new_vehicle))
        row.addStretch()
        row.addWidget(button("Выбрать", lambda: self.table.selected() and self._done(self.table.selected()["id"]), primary=True))
        row.addWidget(button("Отмена", self.reject))
        lay.addLayout(row)
        self.timer = debounce(self, self.load)
        self.q.textChanged.connect(lambda *_: self.timer.start())
        self.load()

    def load(self):
        self.table.set_rows(vehicles.search_vehicles(self.ctx, self.q.text()))

    def new_vehicle(self):
        d = VehicleDialog(self.ctx, self, {"plate": self.q.text()} if self.q.text() else None)
        if d.exec():
            if d.warnings:
                info(self, "\n".join("• " + w for w in d.warnings), "Неполная карточка автомобиля")
            self._done(d.vehicle_id)

    def _done(self, vid):
        self.vehicle_id = vid
        self.accept()

    @classmethod
    def pick(cls, ctx, parent=None):
        d = cls(ctx, parent)
        return d.vehicle_id if d.exec() else None


class WorkOrderEditor(QDialog):
    def __init__(self, ctx, wo_id, parent=None, add_part=None):
        super().__init__(parent)
        self.ctx = ctx
        self.wid = wo_id
        self.page = parent
        self.resize(1250, 800)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = label("")
        self.title.setObjectName("PageTitle")
        top.addWidget(self.title, 1)
        self.status_combo = combo([])
        top.addWidget(label("Перевести в статус:"))
        top.addWidget(self.status_combo)
        top.addWidget(button("Применить", self.change_status, primary=True))
        self.b_reopen = button("Исправить выданный", self.reopen)
        top.addWidget(self.b_reopen)
        lay.addLayout(top)
        self.vehicle_lbl = label("")
        lay.addWidget(self.vehicle_lbl)

        head = QWidget()
        form = QFormLayout(head)
        self.mileage = QLineEdit()
        self.mech = combo([("— не назначен —", None)] + [(e["full_name"], e["id"]) for e in list_employees(ctx)])
        self.complaint = QPlainTextEdit()
        self.diagnosis = QPlainTextEdit()
        self.recommend = QPlainTextEdit()
        self.notes = QLineEdit()
        for w in (self.complaint, self.diagnosis, self.recommend):
            w.setFixedHeight(54)
        hl = QHBoxLayout()
        hl.addWidget(self.mileage)
        hl.addWidget(label("Механик:"))
        hl.addWidget(self.mech)
        hl.addWidget(button("Сохранить шапку", self.save_header))
        hw = QWidget()
        hw.setLayout(hl)
        form.addRow("Пробег, км", hw)
        form.addRow("Жалоба клиента", self.complaint)
        form.addRow("Результаты диагностики", self.diagnosis)
        form.addRow("Рекомендации", self.recommend)
        form.addRow("Примечания", self.notes)
        lay.addWidget(head)

        self.tabs = QTabWidget()
        # работы
        w = QWidget()
        v = QVBoxLayout(w)
        self.services = DataTable([("line_no", "№", "text"), ("name", "Работа", "text"), ("employee", "Механик", "text"), ("qty", "Кол-во / н-ч", "qty"),
                                   ("unit_price", "Цена", "money"), ("discount_amount", "Скидка", "money"), ("amount_net", "Сумма", "money"),
                                   ("is_done", "Выполнена", "bool")], empty_text="Работы не добавлены")
        v.addWidget(self.services, 1)
        r = QHBoxLayout()
        self.svc_buttons = [button("Добавить работу", self.add_service, primary=True), button("Выполнена / не выполнена", self.toggle_done),
                            button("Изменить", self.edit_service), button("Удалить", self.remove_service, danger=True)]
        for b in self.svc_buttons:
            r.addWidget(b)
        r.addStretch()
        v.addLayout(r)
        self.tabs.addTab(w, "Работы")
        # запчасти
        w = QWidget()
        v = QVBoxLayout(w)
        self.parts = DataTable([("line_no", "№", "text"), ("part_name", "Запчасть", "text"), ("oem_number", "Артикул", "text"),
                                ("qty", "Кол-во", "qty"), ("unit", "Ед.", "text"), ("unit_price", "Цена", "money"),
                                ("discount_amount", "Скидка", "money"), ("amount_net", "Сумма", "money"), ("status_title", "Статус", "text"),
                                ("reserved_qty", "В резерве", "qty"), ("issued_qty", "Выдано", "qty"), ("returned_qty", "Возвращено", "qty"),
                                ("is_consumable", "Расходник", "bool")], empty_text="Запчасти не добавлены")
        v.addWidget(self.parts, 1)
        r = QHBoxLayout()
        self.part_buttons = [button("Добавить запчасть", self.add_part, primary=True), button("Зарезервировать", self.reserve),
                             button("Выдать со склада", self.issue), button("Вернуть на склад", self.return_part),
                             button("Изменить количество", self.change_qty), button("Удалить строку", self.remove_part, danger=True)]
        for b in self.part_buttons:
            r.addWidget(b)
        r.addStretch()
        v.addLayout(r)
        v.addWidget(label("Резерв не даёт продать деталь другому клиенту. Списание со склада — кнопкой «Выдать со склада» при установке.", "Muted"))
        self.tabs.addTab(w, "Запчасти и материалы")
        # оплаты
        w = QWidget()
        v = QVBoxLayout(w)
        self.pays = DataTable([("number", "Платёж", "text"), ("doc_date", "Дата", "date"), ("kind_title", "Вид", "text"),
                               ("method_title", "Способ", "text"), ("amount", "Сумма", "money"), ("status_title", "Статус", "text"),
                               ("note", "Комментарий", "text")], empty_text="Оплат не было")
        v.addWidget(self.pays, 1)
        r = QHBoxLayout()
        r.addWidget(button("Принять оплату", self.pay, primary=True))
        r.addWidget(button("Вернуть деньги клиенту", self.refund))
        r.addStretch()
        v.addLayout(r)
        self.tabs.addTab(w, "Оплаты")
        # документы
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(label("Документы формируются из текущих данных — их можно распечатать повторно в любой момент.", "Muted"))
        for code, (title, _) in generator.DOCUMENTS_FOR_WORK_ORDER.items():
            r = QHBoxLayout()
            r.addWidget(label(title), 1)
            r.addWidget(button("Просмотр и печать", lambda _=False, c=code: self.print_doc(c)))
            r.addWidget(button("Сохранить PDF…", lambda _=False, c=code: self.save_doc(c)))
            v.addLayout(r)
        v.addStretch()
        self.tabs.addTab(w, "Документы")
        lay.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        self.totals = label("")
        bottom.addWidget(self.totals, 1)
        self.b_disc = button("Скидка на заказ, %", self.discount)
        bottom.addWidget(self.b_disc)
        bottom.addWidget(button("ИИ-помощник", self.assistant))
        bottom.addWidget(button("Закрыть", self.accept))
        lay.addLayout(bottom)
        self.load()
        if add_part:
            self.tabs.setCurrentIndex(1)
            self.add_part(catalog.get_part(ctx, add_part))

    # ------------------------------------------------------------------
    def load(self):
        wo = work_orders.get_work_order(self.ctx, self.wid)
        self.wo = wo
        self.setWindowTitle(f"Заказ-наряд № {wo['number']}")
        self.title.setText(f"Заказ-наряд № {wo['number']} — {wo['status_title']}")
        self.vehicle_lbl.setText(f"{wo['vehicle']} · {wo['plate']} · VIN {wo['vin'] or '—'} · Клиент: {wo['customer'] or '—'} {wo['customer_phone']} · "
                                 f"Открыт {fmt(wo['opened_at'], 'datetime')}" + (f" · Выдан {fmt(wo['closed_at'], 'datetime')}" if wo["closed_at"] else "")
                                 + (f" · Причина отмены: {wo['cancel_reason']}" if wo["cancel_reason"] else ""))
        self.mileage.setText(str(wo["mileage"] or ""))
        self.mech.setCurrentIndex(max(self.mech.findData(wo["mechanic_id"]), 0))
        self.complaint.setPlainText(wo["complaint"])
        self.diagnosis.setPlainText(wo["diagnosis"])
        self.recommend.setPlainText(wo["recommendations"])
        self.notes.setText(wo["notes"])
        self.services.set_rows(wo["services"])
        self.parts.set_rows([p for p in wo["parts"]])
        self.pays.set_rows(wo["payments"])
        self.status_combo.clear()
        for st in wo["allowed_statuses"]:
            self.status_combo.addItem(WOStatus.TITLES[st], st)
        self.status_combo.setEnabled(bool(wo["allowed_statuses"]))
        editable = wo["editable"]
        for b in self.svc_buttons + self.part_buttons + [self.b_disc]:
            b.setEnabled(editable)
        self.b_reopen.setVisible(wo["status"] == WOStatus.ISSUED and self.ctx.can(Perm.OPERATION_CANCEL))
        closed = wo["status"] == WOStatus.ISSUED
        base = wo["total_amount"] if closed else wo["estimate_amount"]
        gp = ""
        if closed and self.ctx.can(Perm.FINANCE_VIEW):
            gp = f" · Валовая прибыль: {fmt(wo['gross_profit'], 'money')}" + ("" if wo["cogs_complete"] else " (себестоимость неполная)")
        self.totals.setText(
            f"{'Итого' if closed else 'Смета (все строки)'}: {fmt(base, 'money')} · Выполнено: работы {fmt(wo['services_amount'], 'money')}, "
            f"запчасти {fmt(wo['parts_amount'], 'money')}, скидка {fmt(wo['discount_amount'], 'money')} → {fmt(wo['total_amount'], 'money')} · "
            f"Оплачено: {fmt(wo['paid'], 'money')} · {'Долг' if closed else 'К оплате'}: {fmt(wo['debt'] if closed else wo['to_pay'], 'money')}{gp}")

    def save_header(self):
        if run(self, work_orders.update_work_order, self.ctx, self.wid, mileage=self.mileage.text(), mechanic_id=self.mech.currentData(),
               complaint=self.complaint.toPlainText(), diagnosis=self.diagnosis.toPlainText(),
               recommendations=self.recommend.toPlainText(), notes=self.notes.text(), success="Сохранено") is not FAILED:
            self.load()

    def change_status(self):
        st = self.status_combo.currentData()
        if not st:
            return
        reason = ""
        if st == WOStatus.CANCELLED:
            reason = reason_dialog(self, "Отмена заказ-наряда", "Причина отмены")
            if not reason:
                return
        if st == WOStatus.ISSUED:
            debt = self.wo["total_amount"] - self.wo["paid"]
            text = f"Выдать автомобиль? Итог: {fmt(self.wo['total_amount'], 'money')}."
            if debt > 0:
                text += f"\n\nВнимание: не оплачено {fmt(debt, 'money')} — будет учтено как долг клиента."
            if not confirm(self, text):
                return
        self.save_header()
        if run(self, work_orders.change_status, self.ctx, self.wid, st, reason) is not FAILED:
            self.load()

    def reopen(self):
        reason = reason_dialog(self, "Исправление выданного заказ-наряда", "Причина исправления")
        if reason and run(self, work_orders.reopen_issued, self.ctx, self.wid, reason) is not FAILED:
            self.load()

    # ------------------------------------------------------------------ работы
    def _service_dialog(self, row=None):
        d = FormDialog("Работа", self)
        name = d.add("Наименование*", QLineEdit(row["name"] if row else ""))
        sugg = work_orders.service_suggestions(self.ctx, limit=500)
        comp = QCompleter(QStringListModel([s["name"] for s in sugg]), d)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        name.setCompleter(comp)
        price = d.add("Цена*", MoneyEdit(row["unit_price"] if row else None))
        prices = {s["name"]: s["last_price"] for s in sugg}
        name.editingFinished.connect(lambda: (not price.text()) and name.text() in prices and price.set_value(prices[name.text()]))
        qty = d.add("Количество / нормо-часы", QtyEdit(fmt(row["qty"], "qty") if row else "1"))
        emp = d.add("Механик", combo([("— ответственный по заказу —", None)] + [(e["full_name"], e["id"]) for e in list_employees(self.ctx)],
                                     row["employee_id"] if row else None))
        disc = d.add("Скидка, %", QLineEdit(""))
        done = None
        if row is None:
            done = d.add("", QCheckBox("Уже выполнена"))
        d.form.addRow("", label("Если цена не указана, но указаны нормо-часы, используется стоимость нормо-часа из настроек.", "Muted"))
        return d, name, price, qty, emp, disc, done

    def add_service(self):
        d, name, price, qty, emp, disc, done = self._service_dialog()
        d.on_accept = lambda: work_orders.add_service(self.ctx, self.wid, name.text(), price.value(), qty.value() or "1",
                                                      hours=None if price.value() else qty.value(), employee_id=emp.currentData(),
                                                      done=done.isChecked(), discount_percent=disc.text().replace(",", ".") or None)
        if d.exec():
            self.load()

    def edit_service(self):
        row = self.services.selected()
        if not row:
            return info(self, "Выберите работу")
        d, name, price, qty, emp, disc, _ = self._service_dialog(row)
        fields = lambda: {"name": name.text(), "unit_price": price.value(), "qty": qty.value() or "1", "employee_id": emp.currentData(),
                          **({"discount_percent": disc.text().replace(",", ".")} if disc.text().strip() else {})}
        d.on_accept = lambda: work_orders.update_service(self.ctx, row["id"], **fields())
        if d.exec():
            self.load()

    def toggle_done(self):
        row = self.services.selected()
        if row and run(self, work_orders.set_service_done, self.ctx, row["id"], not row["is_done"]) is not FAILED:
            self.load()

    def remove_service(self):
        row = self.services.selected()
        if row and confirm(self, f"Удалить работу «{row['name']}»?") and run(self, work_orders.remove_service, self.ctx, row["id"]) is not FAILED:
            self.load()

    # ------------------------------------------------------------------ запчасти
    def add_part(self, part=None):
        part = part or PartPicker.pick(self.ctx, self, "Запчасть для заказ-наряда")
        if not part:
            return
        d = SaleLineDialog(self.ctx, part, self)
        d.setWindowTitle("Запчасть в заказ-наряд")
        consumable = d.add("", QCheckBox("Расходный материал"))
        reserve = d.add("", QCheckBox("Зарезервировать сразу"))
        reserve.setChecked(True)
        res = {}
        d.on_accept = lambda: res.setdefault("r", work_orders.add_part(self.ctx, self.wid, part["id"], d.qty.value(), d.markup.value(),
                                                                       d.manual.value(), consumable.isChecked(), reserve.isChecked(),
                                                                       d.discount.text().replace(",", ".") or None))
        if d.exec():
            r = res["r"]
            if r["shortage"] > 0:
                info(self, f"Не хватает на складе: {fmt(r['shortage'], 'qty')}. Зарезервировано: {fmt(r['reserved'], 'qty')}.\n"
                           "Оформите закупку или переведите заказ-наряд в статус «Ожидает запчасти».", "Недостаточно на складе")
            self.load()

    def _part_row(self):
        row = self.parts.selected()
        if not row:
            info(self, "Выберите строку запчасти")
        return row

    def reserve(self):
        row = self._part_row()
        if row:
            r = run(self, work_orders.reserve_part, self.ctx, row["id"])
            if r is not FAILED:
                if r < row["qty"] - row["issued_qty"]:
                    info(self, f"Зарезервировано {fmt(r, 'qty')} из {fmt(row['qty'], 'qty')} — свободного остатка недостаточно.")
                self.load()

    def issue(self):
        row = self._part_row()
        if row and confirm(self, f"Выдать со склада «{row['part_name']}» × {fmt(row['qty'] - row['issued_qty'], 'qty')}? Товар будет списан."):
            if run(self, work_orders.issue_part, self.ctx, row["id"], success="Запчасть выдана со склада") is not FAILED:
                self.load()

    def return_part(self):
        row = self._part_row()
        if not row:
            return
        d = FormDialog("Возврат на склад", self, ok_text="Вернуть")
        d.add("Запчасть", label(f"{row['part_name']} · выдано {fmt(row['issued_qty'], 'qty')}"))
        q = d.add("Количество*", QtyEdit(fmt(row["issued_qty"], "qty")))
        reason = d.add("Причина", QLineEdit("Не понадобилась"))
        d.on_accept = lambda: work_orders.return_part(self.ctx, row["id"], q.value(), reason.text())
        if d.exec():
            self.load()

    def change_qty(self):
        row = self._part_row()
        if not row:
            return
        d = FormDialog("Количество", self)
        q = d.add("Количество*", QtyEdit(fmt(row["qty"], "qty")))
        d.on_accept = lambda: work_orders.update_part_qty(self.ctx, row["id"], q.value())
        if d.exec():
            self.load()

    def remove_part(self):
        row = self._part_row()
        if row and confirm(self, f"Удалить «{row['part_name']}» из заказ-наряда? Резерв будет снят.") and \
                run(self, work_orders.remove_part, self.ctx, row["id"]) is not FAILED:
            self.load()

    def discount(self):
        d = FormDialog("Скидка на заказ-наряд", self)
        p = d.add("Скидка на все строки, %", QLineEdit("0"))
        d.on_accept = lambda: work_orders.set_discount(self.ctx, self.wid, p.text().replace(",", "."))
        if d.exec():
            self.load()

    # ------------------------------------------------------------------ оплаты и документы
    def pay(self):
        if payment_dialog(self.ctx, self, kind=PaymentKind.CUSTOMER_PAYMENT, work_order_id=self.wid, suggested=self.wo["to_pay"] or None):
            self.load()

    def refund(self):
        if payment_dialog(self.ctx, self, kind=PaymentKind.CUSTOMER_REFUND, work_order_id=self.wid, title="Возврат денег клиенту"):
            self.load()

    def _make(self, code):
        _, fn = generator.DOCUMENTS_FOR_WORK_ORDER[code]
        return run(self, fn, self.ctx, self.wid)

    def print_doc(self, code):
        path = self._make(code)
        if path is not FAILED:
            printing.print_with_preview(path, self)

    def save_doc(self, code):
        target = save_file(self, "Сохранить PDF", f"{generator.DOCUMENTS_FOR_WORK_ORDER[code][0]} {self.wo['number']}.pdf", "PDF (*.pdf)")
        if target:
            _, fn = generator.DOCUMENTS_FOR_WORK_ORDER[code]
            if run(self, fn, self.ctx, self.wid, target) is not FAILED:
                from autoservice.ui.common import show_in_folder

                show_in_folder(target)

    def assistant(self):
        self.accept()
        self.page.win.go("assistant", "work_order", wo=self.wo)


__all__ = ["RepairsPage", "WorkOrderEditor", "VehiclePicker", "QDialogButtonBox"]
