"""Автомобили и клиенты: быстрый поиск, карточка с историей обслуживания."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from autoservice.application import vehicles
from autoservice.application.documents_store import add_attachment, list_documents
from autoservice.documents import generator, printing
from autoservice.ui.common import (
    FAILED,
    DataTable,
    button,
    debounce,
    fmt,
    info,
    label,
    open_path,
    page_header,
    pick_file,
    run,
)
from autoservice.ui.dialogs import VehicleDialog, edit_customer
from autoservice.ui.pages import Page

VEHICLE_COLS = [("plate", "Госномер", "text"), ("title", "Автомобиль", "text"), ("vin", "VIN", "text"), ("engine_code", "Двигатель", "text"),
                ("mileage", "Пробег", "text"), ("owner", "Владелец", "text"), ("owner_phone", "Телефон", "text")]


class VehiclesPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Автомобили", "Поиск по госномеру (в т.ч. прежнему), VIN, марке и модели, клиенту, номеру заказ-наряда.",
                                           [button("Новый автомобиль", self.new_vehicle, primary=True)]))
        self.tabs = QTabWidget()
        w = QWidget()
        lay = QVBoxLayout(w)
        self.q = QLineEdit()
        self.q.setPlaceholderText("А123ВС77, VIN, Toyota Camry, Иванов, 000128…")
        lay.addWidget(self.q)
        self.table = DataTable(VEHICLE_COLS, empty_text="Автомобили не найдены")
        self.table.activated.connect(lambda r: self.open_card(r["id"]))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button("Открыть карточку", lambda: self.table.selected() and self.open_card(self.table.selected()["id"])))
        row.addWidget(button("Новый заказ-наряд", lambda: self.table.selected() and self.win.go("repairs", "new", vehicle_id=self.table.selected()["id"])))
        row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(w, "Автомобили")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        self.cq = QLineEdit()
        self.cq.setPlaceholderText("ФИО, название организации или телефон")
        l2.addWidget(self.cq)
        self.customers = DataTable([("name", "Клиент", "text"), ("phone", "Телефон", "text"), ("email", "Эл. почта", "text"),
                                    ("inn", "ИНН", "text"), ("notes", "Примечание", "text")], empty_text="Клиенты не найдены")
        self.customers.activated.connect(lambda r: edit_customer(self.ctx, self, r) and self.refresh())
        l2.addWidget(self.customers, 1)
        r2 = QHBoxLayout()
        r2.addWidget(button("Новый клиент", lambda: edit_customer(self.ctx, self) and self.refresh(), primary=True))
        r2.addWidget(button("Изменить", lambda: self.customers.selected() and edit_customer(self.ctx, self, self.customers.selected()) and self.refresh()))
        r2.addStretch()
        l2.addLayout(r2)
        self.tabs.addTab(w2, "Клиенты")
        self.layout_.addWidget(self.tabs, 1)
        self.t1 = debounce(self, self.load_vehicles)
        self.t2 = debounce(self, self.load_customers)
        self.q.textChanged.connect(lambda *_: self.t1.start())
        self.cq.textChanged.connect(lambda *_: self.t2.start())

    def load_vehicles(self):
        self.table.set_rows(vehicles.search_vehicles(self.ctx, self.q.text()))

    def load_customers(self):
        self.customers.set_rows(vehicles.search_customers(self.ctx, self.cq.text()))

    def refresh(self):
        self.load_vehicles()
        self.load_customers()

    def new_vehicle(self):
        d = VehicleDialog(self.ctx, self)
        if d.exec():
            if d.warnings:
                info(self, "Автомобиль сохранён.\n\n" + "\n".join("• " + w for w in d.warnings), "Неполная карточка")
            self.refresh()
            self.open_card(d.vehicle_id)

    def open_card(self, vehicle_id):
        VehicleCard(self.ctx, vehicle_id, self).exec()
        self.refresh()

    def handle(self, action, **kw):
        if action == "new_vehicle":
            self.new_vehicle()
        elif action == "search":
            self.tabs.setCurrentIndex(0)
            self.q.setFocus()
        elif action == "open":
            self.open_card(kw["vehicle_id"])


class VehicleCard(QDialog):
    def __init__(self, ctx, vehicle_id, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.vid = vehicle_id
        self.page = parent
        self.resize(1050, 680)
        lay = QVBoxLayout(self)
        self.title = label("")
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        self.details = label("")
        lay.addWidget(self.details)
        self.warn = label("", "Warning")
        lay.addWidget(self.warn)
        tabs = QTabWidget()
        self.history = DataTable([("number", "Заказ-наряд", "text"), ("opened_at", "Открыт", "date"), ("status_title", "Статус", "text"),
                                  ("mileage", "Пробег", "text"), ("complaint", "Жалоба", "text"), ("diagnosis", "Диагностика", "text"),
                                  ("services_text", "Работы", "text"), ("parts_text", "Запчасти", "text"), ("total", "Сумма", "money")],
                                 empty_text="Обращений ещё не было")
        self.history.activated.connect(lambda r: self._open_wo(r["id"]))
        tabs.addTab(self.history, "История ремонтов")
        self.docs = DataTable([("title", "Документ", "text"), ("original_name", "Файл", "text"), ("created_at", "Добавлен", "datetime")],
                              empty_text="Документов нет")
        self.docs.activated.connect(lambda r: open_path(r["path"]))
        tabs.addTab(self.docs, "Документы")
        self.plates = DataTable([("plate", "Госномер", "text"), ("valid_from", "С", "datetime"), ("valid_to", "По", "datetime")])
        tabs.addTab(self.plates, "История госномеров")
        lay.addWidget(tabs, 1)
        row = QHBoxLayout()
        for text, slot, primary in (("Новый заказ-наряд", self.new_wo, True), ("Изменить данные", self.edit, False),
                                    ("Прикрепить документ", self.attach, False), ("Отчёт по автомобилю", self.report, False),
                                    ("ИИ-помощник", self.assistant, False), ("Закрыть", self.accept, False)):
            row.addWidget(button(text, slot, primary=primary))
        row.addStretch()
        lay.addLayout(row)
        self.load()

    def load(self):
        v = vehicles.get_vehicle(self.ctx, self.vid)
        self.v = v
        self.setWindowTitle(f"Автомобиль {v['plate']} {v['title']}")
        self.title.setText(f"{v['title']} · {v['plate'] or 'без госномера'}")
        vol = f"{fmt(v['engine_volume'], 'qty')} л" if v["engine_volume"] else ""
        self.details.setText(
            f"VIN: {v['vin'] or '—'} · Поколение: {v['generation'] or '—'} · Двигатель: {' '.join(x for x in (v['engine_type'], v['engine_code'], vol, v['fuel']) if x) or '—'} · "
            f"Трансмиссия: {v['transmission'] or '—'} · Привод: {v['drive'] or '—'} · Пробег: {v['mileage'] or '—'} км\n"
            f"Владелец: {v['owner'] or '—'} {v['owner_phone']}")
        self.warn.setText(f"⚠ {v['vin_warning']}" if v["vin_warning"] else "")
        self.history.set_rows([{**h, "services_text": "; ".join(h["services"]), "parts_text": "; ".join(h["parts"])} for h in v["history"]])
        self.docs.set_rows(list_documents(self.ctx, vehicle_id=self.vid))
        self.plates.set_rows(v["plates"])

    def _open_wo(self, wo_id):
        self.accept()
        self.page.win.go("repairs", "open", wo_id=wo_id)

    def new_wo(self):
        self.accept()
        self.page.win.go("repairs", "new", vehicle_id=self.vid)

    def edit(self):
        d = VehicleDialog(self.ctx, self, self.v)
        if d.exec():
            self.load()

    def attach(self):
        path = pick_file(self, "Документ автомобиля", "Документы (*.pdf *.png *.jpg *.jpeg *.docx *.txt *.xlsx)")
        if path and run(self, add_attachment, self.ctx, path, "vehicle", path.stem, vehicle_id=self.vid) is not FAILED:
            self.load()

    def report(self):
        path = run(self, generator.vehicle_report_pdf, self.ctx, self.vid)
        if path is not FAILED:
            printing.print_with_preview(path, self)

    def assistant(self):
        self.accept()
        self.page.win.go("assistant", "vehicle", vehicle_id=self.vid)
