"""Склад: каталог запчастей, остатки, движение, инвентаризация, корректировки, поставщики."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoservice.application import catalog, reports, stock_docs, work_orders
from autoservice.application.reports import ReportFilters
from autoservice.documents import excel
from autoservice.domain.enums import WOStatus
from autoservice.ui.common import (
    FAILED,
    DataTable,
    FormDialog,
    MoneyEdit,
    QtyEdit,
    button,
    combo,
    confirm,
    date_edit,
    debounce,
    fmt,
    info,
    label,
    open_path,
    page_header,
    pick_file,
    qdate,
    run,
    save_file,
)
from autoservice.ui.dialogs import PART_COLUMNS, PartDialog, PartPicker, edit_supplier
from autoservice.ui.pages import Page


class StockPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Склад", "Каталог запчастей, остатки по партиям (FIFO), движение, инвентаризация."))
        self.tabs = QTabWidget()
        self.layout_.addWidget(self.tabs, 1)
        self._catalog_tab()
        self._stock_tab()
        self._moves_tab()
        self._inventory_tab()
        self._adjust_tab()
        self._suppliers_tab()
        self.tabs.currentChanged.connect(lambda *_: self.refresh())

    # ------------------------------------------------------------------ каталог
    def _catalog_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Поиск: артикул (в т.ч. альтернативный и поставщика), название, бренд, код…")
        self.only_stock = QCheckBox("Только в наличии")
        top.addWidget(self.query, 1)
        top.addWidget(self.only_stock)
        lay.addLayout(top)
        split = QSplitter()
        self.cats = QListWidget()
        self.cats.setMaximumWidth(260)
        split.addWidget(self.cats)
        cols = PART_COLUMNS[:6] + [("on_hand", "Остаток", "qty"), ("reserved", "Резерв", "qty")] + PART_COLUMNS[6:]
        self.parts = DataTable(cols, empty_text="Запчастей нет. Добавьте карточку или импортируйте справочник.")
        self.parts.activated.connect(lambda r: self.open_part(r["id"]))
        split.addWidget(self.parts)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        row2 = QHBoxLayout()
        for i, (text, slot, primary) in enumerate((("Новая запчасть", self.new_part, True), ("Открыть карточку", lambda: self._with_part(self.open_part), False),
                                    ("Начальный остаток", lambda: self._with_part(self.opening), False),
                                    ("Списание", lambda: self._with_part(self.write_off), False),
                                    ("Оприходование излишка", lambda: self._with_part(self.surplus), False),
                                    ("В продажу", lambda: self._with_part(lambda pid: self.win.go("sales", "new", part_id=pid)), False),
                                    ("В закупку", lambda: self._with_part(lambda pid: self.win.go("purchases", "new", part_id=pid)), False),
                                    ("В заказ-наряд", lambda: self._with_part(self.to_work_order), False),
                                    ("Импорт справочника", self.import_catalog, False), ("Новая категория", self.new_category, False))):
            (row if i < 5 else row2).addWidget(button(text, slot, primary=primary))
        row.addStretch()
        row2.addStretch()
        lay.addLayout(row)
        lay.addLayout(row2)
        self.tabs.addTab(w, "Каталог запчастей")
        self.timer = debounce(self, self.load_parts)
        self.query.textChanged.connect(lambda *_: self.timer.start())
        self.only_stock.toggled.connect(lambda *_: self.load_parts())
        self.cats.currentRowChanged.connect(lambda *_: self.load_parts())

    def load_categories(self):
        current = self.cats.currentItem().data(Qt.ItemDataRole.UserRole) if self.cats.currentItem() else None
        self.cats.blockSignals(True)
        self.cats.clear()
        all_item = QListWidgetItem("Все категории")
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.cats.addItem(all_item)
        for c in catalog.list_categories(self.ctx):
            item = QListWidgetItem(f"{c['name']} ({c['parts_count']})")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.cats.addItem(item)
            if c["id"] == current:
                self.cats.setCurrentItem(item)
        if self.cats.currentRow() < 0:
            self.cats.setCurrentRow(0)
        self.cats.blockSignals(False)

    def load_parts(self):
        cat = self.cats.currentItem().data(Qt.ItemDataRole.UserRole) if self.cats.currentItem() else None
        rows = catalog.search_parts(self.ctx, self.query.text(), cat, self.only_stock.isChecked())
        for r in rows:
            if r["low_stock"]:
                r["_color"] = "#b45309"
        self.parts.set_rows(rows)

    def _with_part(self, fn):
        row = self.parts.selected()
        if not row:
            return info(self, "Выберите запчасть в таблице")
        fn(row["id"])

    def new_part(self):
        if PartDialog(self.ctx, None, self).exec():
            self.refresh()

    def open_part(self, part_id):
        PartDialog(self.ctx, part_id, self).exec()
        self.refresh()

    def _qty_cost_dialog(self, title, part_id, with_cost: bool, note: str):
        part = catalog.get_part(self.ctx, part_id)
        d = FormDialog(title, self, ok_text="Провести")
        d.add("Запчасть", label(f"{part['name']} ({part['oem_number'] or part['code']})"))
        qty = d.add("Количество*", QtyEdit("1"))
        cost = d.add("Себестоимость за единицу", MoneyEdit()) if with_cost else None
        reason = d.add("Основание*", QLineEdit(note))
        if with_cost:
            d.form.addRow("", label("Если себестоимость неизвестна — оставьте пустым: прибыль по этим единицам не будет показываться.", "Muted"))
        return d, qty, cost, reason

    def opening(self, part_id):
        d, qty, cost, reason = self._qty_cost_dialog("Ввод начального остатка", part_id, True, "Ввод начальных остатков")
        d.on_accept = lambda: stock_docs.enter_opening_balance(self.ctx, part_id, qty.value(), cost.value(), reason.text())
        if d.exec():
            self.refresh()

    def surplus(self, part_id):
        d, qty, cost, reason = self._qty_cost_dialog("Оприходование излишка", part_id, True, "")
        d.on_accept = lambda: stock_docs.register_surplus(self.ctx, part_id, qty.value(), cost.value(), reason.text())
        if d.exec():
            self.refresh()

    def write_off(self, part_id):
        d, qty, _, reason = self._qty_cost_dialog("Списание (брак, порча, утеря)", part_id, False, "")
        d.on_accept = lambda: stock_docs.write_off(self.ctx, part_id, qty.value(), reason.text())
        if d.exec():
            self.refresh()

    def to_work_order(self, part_id):
        orders = work_orders.list_work_orders(self.ctx, WOStatus.OPEN - {WOStatus.READY})
        if not orders:
            return info(self, "Нет открытых заказ-нарядов")
        d = FormDialog("Добавить в заказ-наряд", self, ok_text="Выбрать")
        c = d.add("Заказ-наряд", combo([(f"№ {o['number']} — {o['vehicle']} {o['plate']}", o["id"]) for o in orders]))
        if d.exec():
            self.win.go("repairs", "open", wo_id=c.currentData(), add_part=part_id)

    def import_catalog(self):
        path = pick_file(self, "Импорт справочника запчастей", "Таблицы (*.xlsx *.csv)")
        if not path:
            return
        res = run(self, catalog.import_catalog, self.ctx, path)
        if res is FAILED:
            return
        text = f"Создано: {res['created']}, обновлено: {res['updated']}, пропущено: {res['skipped']}."
        if res["errors"]:
            text += "\n\nОшибки:\n" + "\n".join(res["errors"][:20])
        info(self, text + "\n\nПрименимость к автомобилям при импорте не создаётся — её добавляют с указанием источника.",
             "Импорт справочника")
        self.refresh()

    def new_category(self):
        d = FormDialog("Новая категория", self)
        name = d.add("Название*", QLineEdit())
        d.on_accept = lambda: catalog.add_category(self.ctx, name.text())
        if d.exec():
            self.refresh()

    # ------------------------------------------------------------------ остатки
    def _stock_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.stock_table = DataTable([("code", "Код", "text"), ("article", "Артикул", "text"), ("name", "Наименование", "text"),
                                      ("category", "Категория", "text"), ("location", "Место", "text"), ("on_hand", "Остаток", "qty"),
                                      ("reserved", "Резерв", "qty"), ("available", "Доступно", "qty"), ("min_stock", "Минимум", "qty"),
                                      ("value", "Себестоимость остатка", "money"), ("unknown_qty", "Без себестоимости", "qty"),
                                      ("low", "Заканчивается", "text")], search=True, empty_text="Склад пуст")
        lay.addWidget(self.stock_table, 1)
        self.stock_total = label("", "Muted")
        lay.addWidget(self.stock_total)
        row = QHBoxLayout()
        row.addWidget(button("Экспорт в Excel", self.export_stock))
        row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(w, "Остатки")

    def load_stock(self):
        self.stock_report = reports.run_report(self.ctx, "stock", ReportFilters())
        for r in self.stock_report.rows:
            if r["low"]:
                r["_color"] = "#b45309"
        self.stock_table.set_rows(self.stock_report.rows)
        self.stock_total.setText(f"Себестоимость остатков: {fmt(self.stock_report.totals.get('value'), 'money')}. "
                                 + " ".join(self.stock_report.notes))

    def export_stock(self):
        path = save_file(self, "Сохранить остатки", f"Остатки_{date.today():%Y-%m-%d}.xlsx", "Excel (*.xlsx)")
        if path and run(self, excel.export_xlsx, self.stock_report, path) is not FAILED:
            open_path(path)

    # ------------------------------------------------------------------ движение
    def _moves_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.m1, self.m2 = date_edit(date.today().replace(day=1)), date_edit()
        top.addWidget(label("Период:"))
        top.addWidget(self.m1)
        top.addWidget(self.m2)
        top.addWidget(button("Показать", self.load_moves))
        top.addStretch()
        lay.addLayout(top)
        self.moves = DataTable([("ts", "Дата и время", "datetime"), ("kind_title", "Операция", "text"), ("part_code", "Код", "text"),
                                ("part_name", "Запчасть", "text"), ("qty", "Кол-во", "qty"), ("cost", "Себестоимость", "money"),
                                ("doc_type", "Документ", "text"), ("doc_id", "№", "text"), ("user", "Пользователь", "text"),
                                ("note", "Примечание", "text")], search=True, empty_text="Движений за период нет")
        lay.addWidget(self.moves, 1)
        self.tabs.addTab(w, "Движение товаров")

    def load_moves(self):
        self.moves.set_rows(stock_docs.part_movements(self.ctx, None, qdate(self.m1), qdate(self.m2)))

    # ------------------------------------------------------------------ инвентаризация
    def _inventory_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.inv_list = DataTable([("number", "Номер", "text"), ("doc_date", "Дата", "date"), ("status_title", "Статус", "text"),
                                   ("items", "Позиций", "text"), ("notes", "Примечание", "text")], empty_text="Инвентаризаций не было")
        self.inv_list.activated.connect(lambda r: self.open_inventory(r["id"]))
        lay.addWidget(self.inv_list, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новая инвентаризация", self.new_inventory, primary=True))
        row.addWidget(button("Открыть", lambda: self.inv_list.selected() and self.open_inventory(self.inv_list.selected()["id"])))
        row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(w, "Инвентаризация")

    def new_inventory(self):
        cats = catalog.list_categories(self.ctx)
        d = FormDialog("Новая инвентаризация", self, ok_text="Создать")
        cat = d.add("Категория", combo([("Все запчасти", None)] + [(c["name"], c["id"]) for c in cats]))
        notes = d.add("Примечание", QLineEdit())
        res = {}
        d.on_accept = lambda: res.setdefault("id", stock_docs.create_inventory(self.ctx, None, cat.currentData(), notes.text()))
        if d.exec():
            self.open_inventory(res["id"])

    def open_inventory(self, count_id):
        InventoryDialog(self.ctx, count_id, self).exec()
        self.refresh()

    # ------------------------------------------------------------------ корректировки
    def _adjust_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.adjust = DataTable([("number", "Номер", "text"), ("doc_date", "Дата", "date"), ("kind_title", "Операция", "text"),
                                 ("part_code", "Код", "text"), ("part_name", "Запчасть", "text"), ("qty", "Кол-во", "qty"),
                                 ("cost", "Себестоимость", "money"), ("reason", "Основание", "text")], search=True,
                                empty_text="Корректировок нет")
        lay.addWidget(self.adjust, 1)
        lay.addWidget(label("Корректировки создаются из каталога: «Начальный остаток», «Списание», «Оприходование излишка». "
                            "Проведённые операции не удаляются.", "Muted"))
        self.tabs.addTab(w, "Корректировки")

    # ------------------------------------------------------------------ поставщики
    def _suppliers_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.suppliers = DataTable([("name", "Поставщик", "text"), ("inn", "ИНН", "text"), ("phone", "Телефон", "text"),
                                    ("email", "Эл. почта", "text"), ("contact_person", "Контакт", "text"),
                                    ("lead_time_days", "Срок поставки, дн.", "text"), ("notes", "Примечание", "text")],
                                   search=True, empty_text="Поставщиков нет")
        self.suppliers.activated.connect(lambda r: edit_supplier(self.ctx, self, r) and self.refresh())
        lay.addWidget(self.suppliers, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новый поставщик", lambda: edit_supplier(self.ctx, self) and self.refresh(), primary=True))
        row.addWidget(button("Изменить", lambda: self.suppliers.selected() and edit_supplier(self.ctx, self, self.suppliers.selected()) and self.refresh()))
        row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(w, "Поставщики")

    # ------------------------------------------------------------------
    def refresh(self):
        i = self.tabs.currentIndex()
        if i == 0:
            self.load_categories()
            self.load_parts()
        elif i == 1:
            self.load_stock()
        elif i == 2:
            self.load_moves()
        elif i == 3:
            self.inv_list.set_rows(stock_docs.list_inventories(self.ctx))
        elif i == 4:
            self.adjust.set_rows(stock_docs.list_adjustments(self.ctx))
        elif i == 5:
            from autoservice.application.catalog import list_suppliers

            self.suppliers.set_rows(list_suppliers(self.ctx, include_inactive=True))

    def handle(self, action, **kw):
        if action == "search":
            self.tabs.setCurrentIndex(0)
            self.query.setFocus()
        elif action == "open_part":
            self.open_part(kw["part_id"])
        elif action == "new_part":
            self.new_part()


class InventoryDialog(QDialog):
    def __init__(self, ctx, count_id, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.count_id = count_id
        self.resize(1000, 640)
        lay = QVBoxLayout(self)
        self.title = label("")
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        lay.addWidget(label("Введите фактическое количество. Для излишка можно указать себестоимость единицы (иначе — неизвестна).", "Muted"))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Код", "Наименование", "Артикул", "Место", "Учётный остаток", "Фактический",
                                              "Себестоимость излишка", "Разница"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        btns = QDialogButtonBox()
        self.save_btn = btns.addButton("Сохранить ввод", QDialogButtonBox.ButtonRole.ApplyRole)
        self.fill_btn = btns.addButton("Факт = учёт для пустых", QDialogButtonBox.ButtonRole.ActionRole)
        self.post_btn = btns.addButton("Провести инвентаризацию", QDialogButtonBox.ButtonRole.AcceptRole)
        self.post_btn.setObjectName("Primary")
        close = btns.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        self.save_btn.clicked.connect(self.save)
        self.fill_btn.clicked.connect(self.fill)
        self.post_btn.clicked.connect(self.post)
        close.clicked.connect(self.reject)
        lay.addWidget(btns)
        self.load()

    def load(self):
        inv = stock_docs.get_inventory(self.ctx, self.count_id)
        self.inv = inv
        self.title.setText(f"Инвентаризация {inv['number']} от {fmt(inv['doc_date'], 'date')} — {inv['status_title']}")
        self.table.setRowCount(len(inv["items"]))
        editable = inv["status"] == "draft"
        for r, it in enumerate(inv["items"]):
            vals = [it["part_code"], it["part_name"], it["oem_number"], it["location"], fmt(it["qty_expected"], "qty"),
                    "" if it["qty_actual"] is None else fmt(it["qty_actual"], "qty"),
                    "" if it["unit_cost_for_surplus"] is None else str(it["unit_cost_for_surplus"]).replace(".", ","),
                    "" if it["qty_diff"] is None else fmt(it["qty_diff"], "qty")]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c not in (5, 6) or not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()
        for b in (self.save_btn, self.fill_btn, self.post_btn):
            b.setEnabled(editable)

    def save(self) -> bool:
        for r, it in enumerate(self.inv["items"]):
            actual = self.table.item(r, 5).text().strip().replace(",", ".")
            cost = self.table.item(r, 6).text().strip().replace(",", ".").replace(" ", "")
            if run(self, stock_docs.set_actual, self.ctx, it["id"], actual or None, cost or None) is FAILED:
                return False
        self.load()
        return True

    def fill(self):
        for r in range(self.table.rowCount()):
            if not self.table.item(r, 5).text().strip():
                self.table.item(r, 5).setText(self.table.item(r, 4).text())

    def post(self):
        if not self.save():
            return
        if not confirm(self, "Провести инвентаризацию? Недостачи будут списаны, излишки оприходованы. Отменить проведение нельзя."):
            return
        res = run(self, stock_docs.post_inventory, self.ctx, self.count_id)
        if res is not FAILED:
            info(self, f"Недостач: {res['shortage_lines']} (на {fmt(res['shortage_cost'], 'money')}), излишков: {res['surplus_lines']}.")
            self.load()


__all__ = ["StockPage", "PartPicker"]
