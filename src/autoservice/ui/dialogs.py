"""Диалоги справочников: запчасть, выбор запчасти, поставщик, клиент, автомобиль, платёж, расход, сотрудник."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from autoservice.application import catalog, finance, payments, stock_docs, vehicles
from autoservice.application.context import AppContext
from autoservice.domain.enums import (
    DRIVES,
    ENGINE_TYPES,
    EXPENSE_CATEGORIES,
    FUEL_TYPES,
    TRANSMISSIONS,
    UNITS,
    PaymentKind,
    PaymentMethod,
    Verification,
)
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
    debounce,
    fmt,
    info,
    label,
    pick_file,
    qdate,
    run,
)

PART_COLUMNS = [
    ("code", "Код", "text"), ("oem_number", "Артикул", "text"), ("name", "Наименование", "text"), ("brand", "Бренд", "text"),
    ("category", "Категория", "text"), ("available", "Доступно", "qty"), ("unit", "Ед.", "text"),
    ("unit_cost", "Себестоимость ед.", "money"), ("markup_pct", "Наценка", "percent"), ("unit_price", "Цена продажи", "money"),
    ("location", "Место", "text"),
]


def methods_combo(current: str = PaymentMethod.CASH):
    return combo([(t, k) for k, t in PaymentMethod.TITLES.items()], current)


# --------------------------------------------------------------------------- запчасть


class PartDialog(QDialog):
    def __init__(self, ctx: AppContext, part_id: int | None = None, parent=None, defaults: dict | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.part_id = part_id
        self.setWindowTitle("Карточка запчасти" if part_id else "Новая запчасть")
        self.resize(880, 640)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        main = QWidget()
        from PySide6.QtWidgets import QFormLayout

        form = QFormLayout(main)
        self.name = QLineEdit()
        self.oem = QLineEdit()
        self.brand = QLineEdit()
        self.manufacturer = QLineEdit()
        cats = catalog.list_categories(ctx)
        self.category = combo([("— не выбрана —", None)] + [(c["name"], c["id"]) for c in cats])
        self.unit = combo([(u, u) for u in UNITS], "шт")
        self.min_stock = QtyEdit("0")
        self.location = QLineEdit()
        self.markup = MarkupSelector(allow_auto=True, auto_text="Не задана (стандартная)")
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.active = QCheckBox("Используется")
        self.active.setChecked(True)
        for text, w in (("Наименование*", self.name), ("Оригинальный артикул", self.oem), ("Бренд", self.brand),
                        ("Производитель", self.manufacturer), ("Категория", self.category), ("Единица измерения", self.unit),
                        ("Минимальный остаток", self.min_stock), ("Место хранения", self.location),
                        ("Индивидуальная наценка", self.markup), ("Описание", self.description), ("", self.active)):
            form.addRow(text, w)
        form.addRow("", label("Наценка запчасти применяется, если в операции и у партии наценка не указана.", "Muted"))
        self.tabs.addTab(main, "Основное")
        if part_id:
            self._build_detail_tabs()
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        btns.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        btns.accepted.connect(self.save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        if part_id:
            self.load()
        elif defaults:
            self.name.setText(defaults.get("name", ""))
            self.oem.setText(defaults.get("oem_number", ""))
            if defaults.get("unit") in UNITS:
                self.unit.setCurrentIndex(self.unit.findData(defaults["unit"]))

    def _build_detail_tabs(self):
        # артикулы
        w = QWidget()
        v = QVBoxLayout(w)
        self.numbers = DataTable([("number", "Артикул", "text"), ("brand", "Бренд", "text"), ("kind", "Вид", "text"),
                                  ("source", "Источник", "text"), ("status_title", "Статус", "text")], empty_text="Альтернативных артикулов нет")
        v.addWidget(self.numbers)
        v.addWidget(button("Добавить артикул", self.add_number))
        self.tabs.addTab(w, "Альтернативные артикулы")
        # аналоги
        w = QWidget()
        v = QVBoxLayout(w)
        self.analogs = DataTable([("code", "Код", "text"), ("name", "Наименование", "text"), ("brand", "Бренд", "text"),
                                  ("oem_number", "Артикул", "text"), ("source", "Источник", "text"), ("status_title", "Статус", "text")],
                                 empty_text="Подтверждённых аналогов нет")
        v.addWidget(self.analogs)
        v.addWidget(label("Аналог добавляется только с указанием источника. Похожее название не является основанием.", "Muted"))
        v.addWidget(button("Добавить аналог", self.add_analog))
        self.tabs.addTab(w, "Аналоги")
        # применимость
        w = QWidget()
        v = QVBoxLayout(w)
        self.compat = DataTable([("make", "Марка", "text"), ("model", "Модель", "text"), ("generation", "Поколение", "text"),
                                 ("year_from", "Год с", "text"), ("year_to", "Год по", "text"), ("engine_code", "Двигатель", "text"),
                                 ("source", "Источник", "text"), ("status_title", "Статус", "text")], empty_text="Применимость не указана")
        v.addWidget(self.compat)
        row = QHBoxLayout()
        row.addWidget(button("Добавить применимость", self.add_compat))
        row.addWidget(button("Отметить подтверждённой", lambda: self.set_compat(Verification.CONFIRMED)))
        row.addWidget(button("Удалить запись", self.remove_compat, danger=True))
        row.addStretch()
        v.addLayout(row)
        self.tabs.addTab(w, "Совместимые автомобили")
        # поставщики и цены
        w = QWidget()
        v = QVBoxLayout(w)
        self.suppliers = DataTable([("supplier", "Поставщик", "text"), ("supplier_article", "Артикул поставщика", "text"),
                                    ("last_price", "Последняя цена", "money"), ("last_price_date", "Дата", "date"),
                                    ("lead_time_days", "Срок поставки, дн.", "text")], empty_text="Закупок ещё не было")
        v.addWidget(self.suppliers)
        self.batches = DataTable([("received_at", "Поступление", "datetime"), ("source_ref", "Документ", "text"),
                                  ("qty_received", "Получено", "qty"), ("qty_remaining", "Остаток", "qty"),
                                  ("unit_cost", "Себестоимость ед.", "money"), ("markup_pct", "Наценка партии", "percent")],
                                 empty_text="Партий нет")
        v.addWidget(label("Партии (FIFO — списание с самой ранней):", "Muted"))
        v.addWidget(self.batches)
        row = QHBoxLayout()
        row.addWidget(button("Наценка партии…", self.batch_markup))
        row.addStretch()
        v.addLayout(row)
        self.tabs.addTab(w, "Поставщики и партии")
        # история
        w = QWidget()
        v = QVBoxLayout(w)
        self.moves = DataTable([("ts", "Дата", "datetime"), ("kind_title", "Операция", "text"), ("qty", "Кол-во", "qty"),
                                ("cost", "Себестоимость", "money"), ("user", "Пользователь", "text"), ("note", "Примечание", "text")],
                               empty_text="Движений нет")
        self.prices = DataTable([("ts", "Дата", "datetime"), ("kind", "Вид", "text"), ("amount", "Цена", "money"),
                                 ("percent", "Наценка", "percent"), ("source_doc", "Документ", "text")], empty_text="Изменений цен нет")
        v.addWidget(label("Движение товара:", "Muted"))
        v.addWidget(self.moves)
        v.addWidget(label("История цен и наценок:", "Muted"))
        v.addWidget(self.prices)
        self.tabs.addTab(w, "История")

    def load(self):
        p = catalog.get_part(self.ctx, self.part_id)
        self.name.setText(p["name"])
        self.oem.setText(p["oem_number"])
        self.brand.setText(p["brand"])
        self.manufacturer.setText(p["manufacturer"])
        self.category.setCurrentIndex(max(self.category.findData(p["category_id"]), 0))
        self.unit.setCurrentIndex(max(self.unit.findData(p["unit"]), 0))
        self.min_stock.setText(fmt(p["min_stock"], "qty"))
        self.location.setText(p["location"])
        self.markup.set_value(p["markup_pct"])
        self.description.setPlainText(p["description"])
        self.active.setChecked(p["is_active"])
        kinds = {"oem": "OEM", "aftermarket": "Аналог производителя", "supplier": "Поставщика", "other": "Другой"}
        self.numbers.set_rows([{**n, "kind": kinds.get(n["kind"], n["kind"])} for n in p["numbers"]])
        self.analogs.set_rows(p["analogs"])
        self.compat.set_rows(p["compatibility"])
        self.suppliers.set_rows(p["suppliers"])
        self.batches.set_rows(stock_docs.batches_of(self.ctx, self.part_id))
        self.moves.set_rows(stock_docs.part_movements(self.ctx, self.part_id))
        titles = {"purchase_cost": "Цена закупки", "markup": "Наценка", "sale_price": "Цена продажи"}
        self.prices.set_rows([{**h, "kind": titles.get(h["kind"], h["kind"])} for h in p["price_history"]])

    def data(self) -> dict:
        return {"name": self.name.text(), "oem_number": self.oem.text(), "brand": self.brand.text(),
                "manufacturer": self.manufacturer.text(), "category_id": self.category.currentData(), "unit": self.unit.currentData(),
                "min_stock": self.min_stock.value() or "0", "location": self.location.text(), "markup_pct": self.markup.value(),
                "description": self.description.toPlainText(), "is_active": self.active.isChecked()}

    def save(self):
        if self.part_id:
            if run(self, catalog.update_part, self.ctx, self.part_id, self.data()) is FAILED:
                return
        else:
            new_id = run(self, catalog.create_part, self.ctx, self.data())
            if new_id is FAILED:
                return
            self.part_id = new_id
        self.accept()

    def add_number(self):
        d = FormDialog("Альтернативный артикул", self)
        num, brand = d.add("Артикул*", QLineEdit()), d.add("Бренд", QLineEdit())
        kind = d.add("Вид", combo([("OEM (производитель авто)", "oem"), ("Аналог производителя запчастей", "aftermarket"),
                                   ("Артикул поставщика", "supplier"), ("Другой", "other")], "aftermarket"))
        source = d.add("Источник", QLineEdit())
        status = d.add("Статус", combo([(t, k) for k, t in Verification.TITLES.items()], Verification.NEEDS_CHECK))
        d.on_accept = lambda: catalog.add_part_number(self.ctx, self.part_id, num.text(), brand.text(), kind.currentData(),
                                                      source.text(), status.currentData())
        if d.exec():
            self.load()

    def add_analog(self):
        picked = PartPicker.pick(self.ctx, self, "Выберите запчасть-аналог")
        if not picked:
            return
        d = FormDialog("Подтверждение аналога", self)
        d.add("Аналог", label(f"{picked['name']} ({picked['oem_number']})"))
        source = d.add("Источник*", QLineEdit())
        source.setPlaceholderText("Каталог производителя, кросс-таблица поставщика, проверенная установка…")
        status = d.add("Статус", combo([(t, k) for k, t in Verification.TITLES.items()], Verification.NEEDS_CHECK))
        d.on_accept = lambda: catalog.add_analog(self.ctx, self.part_id, picked["id"], source.text(), status.currentData())
        if d.exec():
            self.load()

    def add_compat(self):
        d = FormDialog("Применимость к автомобилю", self)
        make, model, gen = d.add("Марка*", QLineEdit()), d.add("Модель", QLineEdit()), d.add("Поколение / кузов", QLineEdit())
        y1, y2 = d.add("Год с", QLineEdit()), d.add("Год по", QLineEdit())
        eng = d.add("Код двигателя", QLineEdit())
        source = d.add("Источник*", QLineEdit())
        source.setPlaceholderText("Каталог EPC, документ поставщика, проверенная установка в заказ-наряде № …")
        status = d.add("Статус", combo([(t, k) for k, t in Verification.TITLES.items()], Verification.NEEDS_CHECK))
        d.on_accept = lambda: catalog.add_compatibility(self.ctx, self.part_id, {
            "make": make.text(), "model": model.text(), "generation": gen.text(), "year_from": y1.text(), "year_to": y2.text(),
            "engine_code": eng.text(), "source": source.text(), "verification_status": status.currentData()})
        if d.exec():
            self.load()

    def set_compat(self, status):
        row = self.compat.selected()
        if not row:
            return info(self, "Выберите запись применимости")
        if run(self, catalog.set_compatibility_status, self.ctx, row["id"], status) is not FAILED:
            self.load()

    def remove_compat(self):
        row = self.compat.selected()
        if row and confirm(self, f"Удалить запись применимости {row['make']} {row['model']}?"):
            if run(self, catalog.remove_compatibility, self.ctx, row["id"]) is not FAILED:
                self.load()

    def batch_markup(self):
        row = self.batches.selected()
        if not row:
            return info(self, "Выберите партию")
        d = FormDialog("Наценка партии", self)
        sel = d.add("Наценка", MarkupSelector(auto_text="Не задана"))
        sel.set_value(row["markup_pct"])
        d.on_accept = lambda: stock_docs.set_batch_markup(self.ctx, row["id"], sel.value())
        if d.exec():
            self.load()


class PartPicker(QDialog):
    def __init__(self, ctx: AppContext, parent=None, title: str = "Выбор запчасти", in_stock_only: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(title)
        self.resize(1050, 560)
        self.result_row = None
        self.in_stock_only = in_stock_only
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Артикул, название, бренд или код…")
        cats = catalog.list_categories(ctx)
        self.cat = combo([("Все категории", None)] + [(c["name"], c["id"]) for c in cats])
        self.only_stock = QCheckBox("Только в наличии")
        self.only_stock.setChecked(in_stock_only)
        top.addWidget(self.query, 3)
        top.addWidget(self.cat, 1)
        top.addWidget(self.only_stock)
        top.addWidget(button("Новая запчасть", self.new_part))
        lay.addLayout(top)
        self.table = DataTable(PART_COLUMNS, empty_text="Ничего не найдено. Измените запрос или создайте карточку.")
        self.table.activated.connect(self._choose)
        lay.addWidget(self.table)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Выбрать")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(lambda: self._choose(self.table.selected()))
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.timer = debounce(self, self.refresh)
        self.query.textChanged.connect(lambda *_: self.timer.start())
        self.cat.currentIndexChanged.connect(lambda *_: self.refresh())
        self.only_stock.toggled.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        rows = run(self, catalog.search_parts, self.ctx, self.query.text(), self.cat.currentData(), self.only_stock.isChecked(), limit=300)
        if rows is not FAILED:
            self.table.set_rows(rows)

    def new_part(self):
        d = PartDialog(self.ctx, None, self, {"name": self.query.text()})
        if d.exec() and d.part_id:
            self.result_row = catalog.get_part(self.ctx, d.part_id)
            self.accept()

    def _choose(self, row):
        if row:
            self.result_row = row
            self.accept()

    @classmethod
    def pick(cls, ctx, parent=None, title: str = "Выбор запчасти", in_stock_only: bool = False) -> dict | None:
        d = cls(ctx, parent, title, in_stock_only)
        return d.result_row if d.exec() else None


# --------------------------------------------------------------------------- поставщик, клиент, автомобиль


def edit_supplier(ctx, parent, row: dict | None = None) -> int | None:
    row = row or {}
    d = FormDialog("Поставщик" if row else "Новый поставщик", parent)
    fields = {k: d.add(t, QLineEdit(str(row.get(k) or ""))) for k, t in (
        ("name", "Название*"), ("inn", "ИНН"), ("phone", "Телефон"), ("email", "Эл. почта"), ("contact_person", "Контактное лицо"),
        ("address", "Адрес"), ("lead_time_days", "Срок поставки, дней"), ("notes", "Примечание"))}
    result = {}
    d.on_accept = lambda: result.setdefault("id", catalog.save_supplier(ctx, {k: w.text() for k, w in fields.items()}, row.get("id")))
    return result.get("id") if d.exec() else None


def edit_customer(ctx, parent, row: dict | None = None) -> int | None:
    row = row or {}
    d = FormDialog("Клиент" if row else "Новый клиент", parent)
    kind = d.add("Тип", combo([("Частное лицо", "person"), ("Организация / ИП", "company")], row.get("kind", "person")))
    fields = {k: d.add(t, QLineEdit(str(row.get(k) or ""))) for k, t in (
        ("name", "ФИО / название*"), ("phone", "Телефон"), ("email", "Эл. почта"), ("inn", "ИНН"), ("address", "Адрес"),
        ("notes", "Примечание"))}
    result = {}
    d.on_accept = lambda: result.setdefault("id", vehicles.save_customer(
        ctx, {"kind": kind.currentData(), **{k: w.text() for k, w in fields.items()}}, row.get("id")))
    return result.get("id") if d.exec() else None


class VehicleDialog(FormDialog):
    """Быстрый ввод автомобиля: обязательна только марка. Клиент выбирается или создаётся тут же."""

    def __init__(self, ctx: AppContext, parent=None, vehicle: dict | None = None):
        super().__init__("Автомобиль" if vehicle else "Новый автомобиль", parent, width=620)
        self.ctx = ctx
        self.vehicle = vehicle or {}
        self.vehicle_id = self.vehicle.get("id")
        v = self.vehicle
        self.plate = self.add("Госномер", QLineEdit(v.get("plate", "")))
        self.vin = self.add("VIN / номер кузова", QLineEdit(v.get("vin", "")))
        self.make = self.add("Марка*", QLineEdit(v.get("make", "")))
        self.model = self.add("Модель", QLineEdit(v.get("model", "")))
        self.generation = self.add("Поколение / кузов", QLineEdit(v.get("generation", "")))
        self.year = self.add("Год выпуска", QLineEdit(str(v.get("year") or "")))
        self.engine_type = self.add("Тип двигателя", combo([("", "")] + [(x, x) for x in ENGINE_TYPES], v.get("engine_type", ""), editable=True))
        self.engine_code = self.add("Код двигателя", QLineEdit(v.get("engine_code", "")))
        self.engine_volume = self.add("Объём двигателя, л", QLineEdit(fmt(v.get("engine_volume"), "qty") if v.get("engine_volume") else ""))
        self.fuel = self.add("Топливо", combo([("", "")] + [(x, x) for x in FUEL_TYPES], v.get("fuel", ""), editable=True))
        self.transmission = self.add("Трансмиссия", combo([("", "")] + [(x, x) for x in TRANSMISSIONS], v.get("transmission", ""), editable=True))
        self.drive = self.add("Привод", combo([("", "")] + [(x, x) for x in DRIVES], v.get("drive", ""), editable=True))
        self.mileage = self.add("Пробег, км", QLineEdit(str(v.get("mileage") or "")))
        self.color = self.add("Цвет", QLineEdit(v.get("color", "")))
        owner_row = QHBoxLayout()
        self.owner = combo([("— без владельца —", None)] + [(f"{c['name']} {c['phone']}".strip(), c["id"]) for c in vehicles.search_customers(ctx, limit=5000)],
                           v.get("owner_id"), editable=True)
        owner_row.addWidget(self.owner, 1)
        owner_row.addWidget(button("Новый клиент", self.new_owner))
        host = QWidget()
        host.setLayout(owner_row)
        self.add("Владелец", host)
        self.notes = self.add("Примечание", QLineEdit(v.get("notes", "")))
        self.warnings: list[str] = []
        self.on_accept = self._save

    def new_owner(self):
        cid = edit_customer(self.ctx, self)
        if cid:
            c = vehicles.get_customer(self.ctx, cid)
            self.owner.addItem(f"{c['name']} {c['phone']}".strip(), cid)
            self.owner.setCurrentIndex(self.owner.count() - 1)

    def _save(self):
        data = {"plate": self.plate.text(), "vin": self.vin.text(), "make": self.make.text(), "model": self.model.text(),
                "generation": self.generation.text(), "year": self.year.text(), "engine_type": self.engine_type.currentText(),
                "engine_code": self.engine_code.text(), "engine_volume": self.engine_volume.text(), "fuel": self.fuel.currentText(),
                "transmission": self.transmission.currentText(), "drive": self.drive.currentText(), "mileage": self.mileage.text(),
                "color": self.color.text(), "notes": self.notes.text()}
        owner = combo_value(self.owner)
        if self.vehicle_id:
            self.warnings = vehicles.update_vehicle(self.ctx, self.vehicle_id, {**data, "owner_id": owner})
        else:
            self.vehicle_id, self.warnings = vehicles.create_vehicle(self.ctx, data, owner)


# --------------------------------------------------------------------------- деньги


def payment_dialog(ctx, parent, *, kind: str, work_order_id=None, sale_id=None, purchase_id=None, suggested=None, title: str = "") -> bool:
    d = FormDialog(title or PaymentKind.TITLES[kind], parent, ok_text="Провести платёж")
    amount = d.add("Сумма*", MoneyEdit(suggested))
    method = d.add("Способ", methods_combo())
    day = d.add("Дата", date_edit())
    note = d.add("Комментарий", QLineEdit())
    d.on_accept = lambda: payments.record_payment(ctx, kind=kind, method=method.currentData(), amount=amount.value(),
                                                  work_order_id=work_order_id, sale_id=sale_id, purchase_id=purchase_id,
                                                  doc_date=qdate(day), note=note.text())
    return bool(d.exec())


def expense_dialog(ctx, parent) -> bool:
    d = FormDialog("Новый расход", parent, ok_text="Провести расход")
    cat = d.add("Статья*", combo([(c, c) for c in EXPENSE_CATEGORIES], editable=True))
    amount = d.add("Сумма*", MoneyEdit())
    method = d.add("Способ оплаты", methods_combo())
    day = d.add("Дата", date_edit())
    payee = d.add("Получатель", QLineEdit())
    desc = d.add("Описание", QLineEdit())
    d.on_accept = lambda: finance.add_expense(ctx, cat.currentText(), amount.value(), method.currentData(), qdate(day), payee.text(), desc.text())
    return bool(d.exec())


def employee_dialog(ctx, parent, row: dict | None = None) -> bool:
    row = row or {}
    d = FormDialog("Сотрудник", parent)
    name = d.add("ФИО*", QLineEdit(row.get("full_name", "")))
    pos = d.add("Должность", QLineEdit(row.get("position", "Механик")))
    phone = d.add("Телефон", QLineEdit(row.get("phone", "")))
    active = d.add("", QCheckBox("Работает"))
    active.setChecked(row.get("is_active", True))
    d.on_accept = lambda: finance.save_employee(ctx, {"full_name": name.text(), "position": pos.text(), "phone": phone.text(),
                                                      "is_active": active.isChecked()}, row.get("id"))
    return bool(d.exec())


def reason_dialog(parent, title: str, prompt: str = "Причина") -> str | None:
    d = FormDialog(title, parent, ok_text="Подтвердить")
    text = d.add(prompt + "*", QLineEdit())

    def _require():
        if not text.text().strip():
            from autoservice.domain.errors import ValidationError

            raise ValidationError(f"Заполните поле «{prompt}»")

    d.on_accept = _require
    return text.text().strip() if d.exec() else None


__all__ = ["PartDialog", "PartPicker", "VehicleDialog", "edit_supplier", "edit_customer", "payment_dialog", "expense_dialog",
           "employee_dialog", "reason_dialog", "PART_COLUMNS", "methods_combo", "Decimal", "QSpinBox", "pick_file"]
