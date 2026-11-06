"""Снимает кадры для видеоурока: настоящая программа на демонстрационных данных.

python tools/tutorial/capture.py <папка_кадров>
Работает во временной папке данных, рабочую базу не трогает.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPoint, QSize  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix="as-tutorial-"))
SHOWN_ROOT = r"C:\ProgramData\AutoServiceAI"
W, H = 1920, 838

app = QApplication([])
from autoservice.ui.theme import STYLE  # noqa: E402

app.setStyleSheet(STYLE)
app.setFont(QFont("Segoe UI", 10))

from autoservice.ai import invoice_import  # noqa: E402
from autoservice.application import auth, bootstrap, catalog, finance, payments, purchasing, sales, stock_docs, vehicles, work_orders  # noqa: E402
from autoservice.application.settings_service import update_settings  # noqa: E402
from autoservice.backup.service import create_backup  # noqa: E402
from autoservice.config.paths import AppPaths  # noqa: E402
from autoservice.domain.enums import PaymentKind, Role, Verification, WOStatus  # noqa: E402
from autoservice.knowledge import library  # noqa: E402
from autoservice.ui.common import DataTable  # noqa: E402

meta: dict = {}
CAPTURE: dict = {}


def settle(n: int = 10):
    for _ in range(n):
        app.processEvents()


def scrub(widget):
    """Скрывает временные пути: показываем путь, как у установленной программы."""
    tmp = str(DATA)
    for lbl in widget.findChildren(QLabel):
        if tmp in lbl.text():
            lbl.setText(lbl.text().replace(tmp, SHOWN_ROOT))
    for table in widget.findChildren(DataTable):
        changed = False
        for row in table.model.rows:
            for k, v in row.items():
                if isinstance(v, str) and tmp in v:
                    row[k] = v.replace(tmp, SHOWN_ROOT)
                    changed = True
        if changed:
            table.model.layoutChanged.emit()


def rects(root, widgets):
    out = []
    for w in widgets:
        if w is None or not w.isVisible():
            continue
        p = w.mapTo(root, QPoint(0, 0))
        out.append([p.x(), p.y(), w.width(), w.height()])
    return out


def grab(name, widget, hl=(), kind="window", base=None):
    settle()
    scrub(widget)
    settle(3)
    widget.grab().save(str(OUT / f"{name}.png"))
    meta[name] = {"kind": kind, "size": [widget.width(), widget.height()], "highlights": rects(widget, hl), "base": base}
    print("кадр", name)


def save_base(name, widget):
    settle()
    scrub(widget)
    widget.grab().save(str(OUT / f"{name}__base.png"))
    return f"{name}__base.png"


def fake_exec(self):
    spec = CAPTURE.pop("next", None)
    if spec is None:
        return 0
    name, prep, hl, base = spec
    self.adjustSize()
    self.show()
    settle()
    if prep:
        prep(self)
        settle()
    grab(name, self, hl(self) if hl else [], "dialog", base)
    self.hide()
    return 0


QDialog.exec = fake_exec


def capture_dialog(name, trigger, base_widget, prep=None, hl=None):
    base = save_base(name, base_widget)
    CAPTURE["next"] = (name, prep, hl, base)
    trigger()
    if "next" in CAPTURE:
        CAPTURE.pop("next")
        raise RuntimeError(f"Диалог {name} не был открыт")


def show_dialog(name, dlg, base_widget, hl=(), size=None):
    base = save_base(name, base_widget)
    if size:
        dlg.resize(*size)
    else:
        dlg.adjustSize()
    dlg.show()
    settle()
    grab(name, dlg, hl if not callable(hl) else hl(dlg), "dialog", base)
    dlg.hide()


def message(name, text, base_widget, title="Подтверждение", icon=QMessageBox.Icon.Question):
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, base_widget)
    box.button(QMessageBox.StandardButton.Yes).setText("Да")
    box.button(QMessageBox.StandardButton.No).setText("Нет")
    show_dialog(name, box, base_widget)


def btn(widget, text):
    from PySide6.QtWidgets import QPushButton

    for b in widget.findChildren(QPushButton):
        if b.text() == text and b.isVisible():
            return b
    return None


def field(dialog, label_text):
    """Виджет поля FormDialog/QFormLayout по тексту подписи."""
    from PySide6.QtWidgets import QFormLayout

    for form in dialog.findChildren(QFormLayout):
        for i in range(form.rowCount()):
            lbl = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            fld = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if lbl and lbl.widget() and lbl.widget().text().startswith(label_text) and fld:
                return fld.widget()
    return None


def editor(dlg, size=(1560, 838)):
    dlg.resize(*size)
    dlg.show()
    settle()
    return dlg


# =========================================================================== 1. первый запуск
res = bootstrap.start(AppPaths(DATA), with_logging=False)
ctx0 = res.ctx
from autoservice.ui.main_window import FirstRunDialog, LoginDialog, MainWindow  # noqa: E402

frd = FirstRunDialog(ctx0)
frd.company.setText("Автосервис «Мотор»")
frd.full_name.setText("Петров Сергей Николаевич")
frd.p1.setText("Пароль-2026")
frd.p2.setText("Пароль-2026")
frd.markup.set_value("30")
frd.manager.setChecked(True)
frd.m_pass.setText("Менеджер-1")
frd.adjustSize()
frd.show()
grab("first_run", frd, [frd.markup, btn(frd, "Начать работу")], "dialog")
frd.hide()

admin = auth.create_first_admin(ctx0, "admin", "Admin-pass-1", "Петров Сергей Николаевич")
ctx = ctx0.as_user(admin)
update_settings(ctx, {"company.name": "Автосервис «Мотор»", "pricing.default_markup": "30"})
auth.create_user(ctx, "manager", "Manager-pass-1", Role.MANAGER, "Смирнова Анна")

lg = LoginDialog(ctx0)
lg.username.setText("admin")
lg.password.setText("Admin-pass-1")
lg.adjustSize()
lg.show()
grab("login", lg, [lg.username, lg.password], "dialog")
lg.hide()

win = MainWindow(ctx)
win.timer.stop()
win.resize(W, H)
win.show()
win.statusBar().showMessage(f"Данные: {SHOWN_ROOT}")
win.go("dashboard")
grab("dash_empty", win, [win.nav["dashboard"]])

# =========================================================================== 2. настройки
update_settings(ctx, {
    "company.legal_name": "ООО «Мотор» (пример)", "company.inn": "7700000000", "company.address": "г. Москва, ул. Примерная, д. 1",
    "company.phone": "+7 (900) 000-00-00", "pricing.labor_rate": "1500", "tax.regime": "УСН «Доходы»", "tax.vat_mode": "not_payer",
})
finance.save_employee(ctx, {"full_name": "Кузнецов Андрей", "position": "Механик", "phone": "+7 900 100-00-01"})
finance.save_employee(ctx, {"full_name": "Волков Дмитрий", "position": "Механик-диагност", "phone": "+7 900 100-00-02"})
sp = win.pages["settings"]
win.go("settings")
sp.tabs.setCurrentIndex(0)
grab("settings_company", win, [sp.company["company.name"], sp.company["company.inn"], btn(sp, "Сохранить реквизиты")])
sp.tabs.setCurrentIndex(1)
grab("settings_pricing", win, [sp.default_markup, sp.labor_rate, sp.vat_mode])
sp.tabs.setCurrentIndex(3)
win.go("settings")
grab("settings_users", win, [sp.users, btn(sp, "Новый пользователь")])
sp.tabs.setCurrentIndex(4)
grab("settings_employees", win, [sp.emps])

# =========================================================================== 3. склад и каталог
s1 = catalog.save_supplier(ctx, {"name": "ООО «Автодеталь-Опт»", "inn": "7700000001", "phone": "+7 900 200-00-01", "lead_time_days": 2})
s2 = catalog.save_supplier(ctx, {"name": "ИП Сидоров А. В.", "inn": "770000000012", "phone": "+7 900 200-00-02", "lead_time_days": 5})
with ctx.db.read() as s:
    cat = {c.code: c.id for c in s.execute(__import__("sqlalchemy").select(__import__("autoservice.persistence.models", fromlist=["PartCategory"]).PartCategory)).scalars()}

stock = win.pages["stock"]
win.go("stock")
from autoservice.ui.dialogs import PartDialog  # noqa: E402

pd = PartDialog(ctx, None, win)
pd.name.setText("Фильтр масляный")
pd.oem.setText("90915-YZZE1")
pd.brand.setText("Toyota")
pd.category.setCurrentIndex(pd.category.findData(cat["filters"]))
pd.min_stock.setText("5")
pd.location.setText("Стеллаж А-3")
pd.markup.set_value("30")
show_dialog("part_new", pd, win, lambda d: [d.name, d.oem, d.category, d.markup], size=(880, 640))

P = {}
P["oil"] = catalog.create_part(ctx, {"name": "Масло моторное 5W-30", "oem_number": "OIL-5W30", "brand": "Total", "unit": "л", "category_id": cat["fluids"], "min_stock": "20", "location": "Стеллаж Б-1"})
P["filter"] = catalog.create_part(ctx, {"name": "Фильтр масляный", "oem_number": "90915-YZZE1", "brand": "Toyota", "category_id": cat["filters"], "min_stock": "5", "location": "Стеллаж А-3", "markup_pct": "30"})
P["pads"] = catalog.create_part(ctx, {"name": "Колодки тормозные передние", "oem_number": "04465-33471", "brand": "Toyota", "unit": "компл", "category_id": cat["brakes"], "min_stock": "2", "location": "Стеллаж В-2"})
P["plug"] = catalog.create_part(ctx, {"name": "Свеча зажигания", "oem_number": "SK20R11", "brand": "Denso", "category_id": cat["electrical"], "min_stock": "8", "location": "Стеллаж А-1"})
P["air"] = catalog.create_part(ctx, {"name": "Фильтр воздушный", "oem_number": "17801-0H050", "brand": "Toyota", "category_id": cat["filters"], "min_stock": "3", "location": "Стеллаж А-4"})
P["coolant"] = catalog.create_part(ctx, {"name": "Антифриз G12", "oem_number": "AF-G12-5", "brand": "Felix", "unit": "л", "category_id": cat["fluids"], "min_stock": "10", "location": "Стеллаж Б-2"})
catalog.add_part_number(ctx, P["filter"], "W 68/3", "MANN-FILTER", "aftermarket", "Каталог MANN-FILTER", Verification.NEEDS_CHECK)
catalog.add_compatibility(ctx, P["filter"], {"make": "Toyota", "model": "Camry", "year_from": "2011", "year_to": "2021", "engine_code": "2AR-FE",
                                              "source": "Электронный каталог Toyota (EPC)", "verification_status": Verification.CONFIRMED})
catalog.add_compatibility(ctx, P["filter"], {"make": "Toyota", "model": "RAV4", "year_from": "2013", "year_to": "2019", "source": "Кросс-таблица поставщика",
                                              "verification_status": Verification.NEEDS_CHECK})

pd2 = PartDialog(ctx, P["filter"], win)
pd2.tabs.setCurrentIndex(3)
show_dialog("part_compat", pd2, win, lambda d: [d.compat], size=(1100, 640))

stock.tabs.setCurrentIndex(0)
stock.refresh()
capture_dialog("opening_balance", lambda: stock.opening(P["plug"]), win,
               prep=lambda d: (field(d, "Количество").setText("16"), field(d, "Себестоимость").setText("380")),
               hl=lambda d: [field(d, "Количество"), field(d, "Себестоимость")])
stock_docs.enter_opening_balance(ctx, P["plug"], "16", "380")

# =========================================================================== 4. закупки
pur = win.pages["purchases"]
win.go("purchases")
capture_dialog("purchase_new", lambda: pur.new(), win,
               prep=lambda d: (field(d, "Поставщик").setCurrentIndex(field(d, "Поставщик").findText("ООО «Автодеталь-Опт»")),
                               field(d, "Номер документа").setText("УПД-1532"), field(d, "Доставка").setText("600")),
               hl=lambda d: [field(d, "Поставщик"), field(d, "Номер документа"), field(d, "Доставка")])
po = purchasing.create_purchase(ctx, s1, "УПД-1532", supplier_doc_date=date.today(), extra_costs="600")
purchasing.add_purchase_line(ctx, po, P["oil"], "40", "480")
purchasing.add_purchase_line(ctx, po, P["filter"], "10", "450")
purchasing.add_purchase_line(ctx, po, P["pads"], "4", "2900", markup_pct="25")
purchasing.add_purchase_line(ctx, po, P["air"], "6", "700")
purchasing.add_purchase_line(ctx, po, P["coolant"], "10", "260")
from autoservice.ui.pages.purchases import InvoiceReviewDialog, PurchaseEditor  # noqa: E402

pe = editor(PurchaseEditor(ctx, po, win))
grab("purchase_draft", pe, [pe.lines, pe.b_post, pe.totals])
capture_dialog("purchase_line", lambda: pe.add_line(catalog.get_part(ctx, P["pads"])), pe,
               prep=lambda d: (field(d, "Количество").setText("4"), field(d, "Цена").setText("2900"), field(d, "Наценка партии").set_value("25")),
               hl=lambda d: [field(d, "Цена"), field(d, "Наценка партии")])
message("purchase_confirm", "Провести закупку ЗК-000001 на 47 700,00 ₽?\nОстатки увеличатся на 5 позиций. Изменить проведённую закупку нельзя.", pe)
purchasing.post_purchase(ctx, po)
pe.load()
grab("purchase_posted", pe, [pe.b_pay, pe.b_return, pe.totals])
capture_dialog("supplier_payment", pe.pay, pe, prep=lambda d: field(d, "Сумма").setText("30000"), hl=lambda d: [field(d, "Сумма"), field(d, "Способ")])
payments.record_payment(ctx, kind=PaymentKind.SUPPLIER_PAYMENT, method="transfer", amount="30000", purchase_id=po)
pe.hide()
po2 = purchasing.create_purchase(ctx, s2, "Н-77", extra_costs="0")
purchasing.add_purchase_line(ctx, po2, P["pads"], "2", "3100")
purchasing.post_purchase(ctx, po2)
win.go("purchases")
pur.tabs.setCurrentIndex(0)
grab("purchases_list", win, [pur.table])

inv = DATA / "Счёт_1601.txt"
inv.write_text("""ООО «Автодеталь-Опт»
Поставщик: ООО «Автодеталь-Опт», ИНН 7700000001
Счёт на оплату № 1601 от 05.11.2026
№ Артикул Наименование Кол-во Ед. Цена Сумма
1 90915-YZZE1 Фильтр масляный Toyota 10 шт 455,00 4 550,00
2 OIL-5W30 Масло моторное 5W-30 20 л 485,00 9 700,00
3 0986494524 Колодки тормозные задние Bosch 2 компл 2 400,00 4 900,00
Итого: 19 150,00
""", encoding="utf-8")
imp = invoice_import.import_invoice(ctx, inv)
ird = InvoiceReviewDialog(ctx, imp, win)
show_dialog("invoice_review", ird, win, lambda d: [d.warn, d.table, d.b_confirm], size=(1500, 760))

# =========================================================================== 5. продажи
sal = win.pages["sales"]
win.go("sales")
sid = sales.create_sale(ctx, customer_name="Розничный покупатель")
from autoservice.ui.pages.sales import SaleEditor, SaleLineDialog  # noqa: E402

se = editor(SaleEditor(ctx, sid, win), (1500, 760))
line = SaleLineDialog(ctx, catalog.search_parts(ctx, "90915")[0], se)
line.qty.setText("1")
line.markup.set_value("30")
line.recalc()
show_dialog("sale_line", line, se, lambda d: [d.markup, d.preview])
sales.add_sale_line(ctx, sid, P["filter"], "1", markup_override="30")
sales.add_sale_line(ctx, sid, P["coolant"], "5", markup_override="40")
se.load()
grab("sale_draft", se, [se.lines, se.b_post, se.totals])
capture_dialog("sale_post", se.post, se, hl=lambda d: [field(d, "Оплата"), field(d, "Получено")])
sales.post_sale(ctx, sid, "cash")
se.load()
grab("sale_posted", se, [se.b_doc, se.b_receipt, se.b_return])
se.lines.view.selectRow(1)
capture_dialog("sale_return", se.make_return, se, prep=lambda d: (field(d, "Количество").setText("1"), field(d, "Причина").setText("Не подошёл объём")),
               hl=lambda d: [field(d, "Количество"), field(d, "Вернуть деньги")])
se.hide()


def pdf_page(name, pdf_path, page=0, width=1100):
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    doc.load(str(pdf_path))
    sz = doc.pagePointSize(page)
    img = doc.render(page, QSize(width, int(width * sz.height() / sz.width())))
    img.save(str(OUT / f"{name}.png"))
    meta[name] = {"kind": "pdf", "size": [img.width(), img.height()], "highlights": [], "base": None}
    doc.close()
    print("кадр", name)


from autoservice.documents import generator  # noqa: E402

pdf_page("receipt_pdf", generator.thermal_receipt_pdf(ctx, "sale", sid, width_mm=80), width=520)

# =========================================================================== 6. автомобили
c1 = vehicles.save_customer(ctx, {"name": "Иванов Иван Иванович", "phone": "+7 900 111-22-33"})
c2 = vehicles.save_customer(ctx, {"name": "ООО «ТаксиПарк»", "kind": "company", "phone": "+7 900 333-44-55", "inn": "7700000099"})
veh = win.pages["vehicles"]
win.go("vehicles")
from autoservice.ui.dialogs import VehicleDialog  # noqa: E402

vd = VehicleDialog(ctx, win)
for w_, t in ((vd.plate, "А123ВС77"), (vd.vin, "JTNBE46K803012345"), (vd.make, "Toyota"), (vd.model, "Camry"), (vd.generation, "XV50"),
              (vd.year, "2018"), (vd.engine_code, "2AR-FE"), (vd.engine_volume, "2,5"), (vd.mileage, "84500")):
    w_.setText(t)
vd.fuel.setCurrentText("Бензин")
vd.transmission.setCurrentText("Автоматическая")
vd.owner.setCurrentIndex(vd.owner.findData(c1))
show_dialog("vehicle_new", vd, win, lambda d: [d.plate, d.vin, d.make, d.owner])
v1, _ = vehicles.create_vehicle(ctx, {"plate": "А123ВС77", "vin": "JTNBE46K803012345", "make": "Toyota", "model": "Camry", "generation": "XV50", "year": "2018",
                                       "engine_code": "2AR-FE", "engine_volume": "2.5", "fuel": "Бензин", "transmission": "Автоматическая", "drive": "Передний",
                                       "mileage": "84500"}, owner_id=c1)
v2, _ = vehicles.create_vehicle(ctx, {"plate": "В456ОР799", "vin": "Z94C241BBLR123456", "make": "Kia", "model": "Rio", "year": "2020", "fuel": "Бензин",
                                       "mileage": "152000"}, owner_id=c2)

# =========================================================================== 7. ремонты
rep = win.pages["repairs"]
win.go("repairs")
capture_dialog("wo_new", lambda: rep.new(v1), win,
               prep=lambda d: (field(d, "Пробег").setText("86200"),
                               field(d, "Ответственный механик").setCurrentIndex(field(d, "Ответственный механик").findText("Волков Дмитрий")),
                               field(d, "Жалоба").setPlainText("Плановое ТО. Горит индикатор «Check Engine», ошибка P0171, неровный холостой ход.")),
               hl=lambda d: [field(d, "Пробег"), field(d, "Жалоба")])
emps = {e["full_name"]: e["id"] for e in finance.list_employees(ctx)}
wo = work_orders.create_work_order(ctx, v1, "Плановое ТО. Горит индикатор «Check Engine», ошибка P0171, неровный холостой ход.", "86200",
                                   mechanic_id=emps["Волков Дмитрий"])
from autoservice.ui.pages.repairs import WorkOrderEditor  # noqa: E402

we = editor(WorkOrderEditor(ctx, wo, rep))
we.tabs.setCurrentIndex(0)
capture_dialog("wo_service", we.add_service, we,
               prep=lambda d: (field(d, "Наименование").setText("Компьютерная диагностика"), field(d, "Цена").setText("1500")),
               hl=lambda d: [field(d, "Наименование"), field(d, "Цена"), field(d, "Механик")])
work_orders.add_service(ctx, wo, "Компьютерная диагностика", "1500", employee_id=emps["Волков Дмитрий"], done=True)
work_orders.add_service(ctx, wo, "Замена моторного масла и масляного фильтра", "1200", employee_id=emps["Кузнецов Андрей"])
work_orders.add_service(ctx, wo, "Замена воздушного фильтра", "400", employee_id=emps["Кузнецов Андрей"])
work_orders.add_service(ctx, wo, "Замена патрубка впуска", "900", employee_id=emps["Кузнецов Андрей"])
we.load()
grab("wo_services", we, [we.services, btn(we, "Выполнена / не выполнена")])
we.tabs.setCurrentIndex(1)
parts_ids = [work_orders.add_part(ctx, wo, P["oil"], "4.5", markup_override="40")["id"],
             work_orders.add_part(ctx, wo, P["filter"], "1")["id"],
             work_orders.add_part(ctx, wo, P["air"], "1", markup_override="30")["id"]]
we.load()
grab("wo_parts_reserved", we, [we.parts, btn(we, "Выдать со склада")])
for pid_ in parts_ids:
    work_orders.issue_part(ctx, pid_)
for sv in work_orders.get_work_order(ctx, wo)["services"]:
    if not sv["is_done"]:
        work_orders.set_service_done(ctx, sv["id"])
work_orders.update_work_order(ctx, wo, diagnosis="Подсос воздуха через трещину патрубка после ДМРВ. Патрубок заменён, ошибка удалена, повторная проверка без ошибок.")
work_orders.change_status(ctx, wo, WOStatus.IN_PROGRESS)
we.load()
grab("wo_parts_issued", we, [we.parts])
work_orders.change_status(ctx, wo, WOStatus.READY)
we.load()
we.status_combo.setCurrentIndex(we.status_combo.findData(WOStatus.ISSUED))
grab("wo_ready", we, [we.status_combo, btn(we, "Применить"), we.totals])
message("wo_issue_confirm", "Выдать автомобиль? Итог: 8 470,00 ₽.\n\nВнимание: не оплачено 8 470,00 ₽ — будет учтено как долг клиента.", we)
work_orders.change_status(ctx, wo, WOStatus.ISSUED)
we.load()
we.tabs.setCurrentIndex(2)
capture_dialog("wo_payment", we.pay, we, prep=lambda d: field(d, "Сумма").setText("5000"), hl=lambda d: [field(d, "Сумма"), field(d, "Способ")])
payments.record_payment(ctx, kind=PaymentKind.CUSTOMER_PAYMENT, method="card", amount="5000", work_order_id=wo)
we.load()
grab("wo_payments", we, [we.pays, we.totals])
we.tabs.setCurrentIndex(3)
grab("wo_documents", we, [we.tabs])
we.hide()
pdf_page("wo_pdf", generator.work_order_pdf(ctx, wo))
pdf_page("act_pdf", generator.act_pdf(ctx, wo))

wo2 = work_orders.create_work_order(ctx, v2, "Стук в передней подвеске, скрип при торможении", "152300", mechanic_id=emps["Кузнецов Андрей"])
work_orders.add_service(ctx, wo2, "Диагностика подвески и тормозной системы", "800")
work_orders.add_part(ctx, wo2, P["pads"], "1", markup_override="25")
work_orders.change_status(ctx, wo2, WOStatus.DIAGNOSTICS)
win.go("repairs")
rep.status.setCurrentIndex(1)
rep.refresh()
grab("repairs_list", win, [rep.table])

win.go("vehicles")
veh.q.setText("Camry")
veh.load_vehicles()
grab("vehicles_list", win, [veh.q, veh.table])
from autoservice.ui.pages.vehicles import VehicleCard  # noqa: E402

vc = VehicleCard(ctx, v1, veh)
show_dialog("vehicle_card", vc, win, lambda d: [d.history], size=(1300, 700))

# =========================================================================== 8. склад: остатки, инвентаризация
win.go("stock")
stock.tabs.setCurrentIndex(0)
stock.refresh()
grab("stock_catalog", win, [stock.query, stock.parts])
stock.tabs.setCurrentIndex(1)
stock.refresh()
grab("stock_balances", win, [stock.stock_table])
cnt = stock_docs.create_inventory(ctx, None, cat["filters"])
from autoservice.ui.pages.stock import InventoryDialog  # noqa: E402

idlg = InventoryDialog(ctx, cnt, win)
for r in range(idlg.table.rowCount()):
    idlg.table.item(r, 5).setText(idlg.table.item(r, 4).text() if r else "7")
show_dialog("inventory", idlg, win, lambda d: [d.table, d.post_btn], size=(1300, 560))
stock.tabs.setCurrentIndex(2)
stock.refresh()
grab("stock_moves", win, [stock.moves])

# =========================================================================== 9. библиотека и ИИ
library.add_note(ctx, {"title": "Регламент ТО: Toyota Camry XV50, двигатель 2AR-FE", "source": "Сервисная книжка владельца", "kind": "regulation",
                       "make": "Toyota", "model": "Camry", "engine": "2AR-FE", "verification_status": Verification.CONFIRMED},
                 "Привод ГРМ — цепь, ремень ГРМ не обслуживается. Замена масла и масляного фильтра — по регламенту производителя. "
                 "Объём масла и допуски сверять с руководством по эксплуатации конкретного автомобиля.")
library.add_note(ctx, {"title": "P0171 на Camry: проверка подсоса воздуха", "source": "Опыт мастера Волкова Д.", "kind": "procedure",
                       "make": "Toyota", "model": "Camry"},
                 "При ошибке P0171 сначала осмотреть патрубок между ДМРВ и дроссельной заслонкой — трещины на сгибе. "
                 "Проверить дымогенератором. Затем топливные коррекции и показания ДМРВ.")
lib = win.pages["library"]
win.go("library")
lib.q.setText("подсос воздуха")
lib.make.setText("Toyota")
lib.search()
grab("library_search", win, [lib.q, lib.results])
lib.tabs.setCurrentIndex(1)
grab("library_rules", win, [lib.rules])
lib.tabs.setCurrentIndex(0)

ast = win.pages["assistant"]
win.go("assistant")
ast.set_vehicle(v1)
ast.complaint.setPlainText("Снова горит «Check Engine», ошибка P0171, неустойчивые обороты на холостом ходу")
ast.run_basic()
grab("assistant_result", win, [ast.out, btn(ast, "Проанализировать (режим A)")])
ast.out.verticalScrollBar().setValue(ast.out.verticalScrollBar().maximum() // 2)
grab("assistant_result2", win, [ast.out])
ast.tabs.setCurrentIndex(1)
ast.load_recs()
grab("assistant_recs", win, [ast.recs, btn(ast, "Создать черновики закупок для выбранных")])

# =========================================================================== 10. финансы и отчёты
finance.add_expense(ctx, "Аренда", "60000", "transfer", payee="ИП Арендодатель", description="Аренда бокса за ноябрь")
finance.add_expense(ctx, "Коммунальные услуги", "8500", "transfer", description="Электроэнергия")
fin = win.pages["finance"]
win.go("finance")
fin.tabs.setCurrentIndex(0)
grab("finance_summary", win, [fin.summary])
fin.tabs.setCurrentIndex(2)
capture_dialog("expense_new", fin.new_expense, win, prep=lambda d: field(d, "Сумма").setText("4500"), hl=lambda d: [field(d, "Статья"), field(d, "Сумма")])
grab("finance_expenses", win, [fin.expenses])
fin.tabs.setCurrentIndex(3)
grab("finance_debts", win, [fin.cust])

repg = win.pages["reports"]
win.go("reports")
for i in range(repg.list.count()):
    if repg.list.item(i).text() == "Сводный финансовый отчёт":
        repg.list.setCurrentRow(i)
grab("reports_summary", win, [repg.list, btn(repg, "Excel (.xlsx)"), btn(repg, "PDF"), btn(repg, "Печать")])
for i in range(repg.list.count()):
    if repg.list.item(i).text() == "Продажи за период":
        repg.list.setCurrentRow(i)
grab("reports_sales", win, [repg.table])

win.go("documents")
grab("documents_page", win, [win.pages["documents"].tabs])

win.go("dashboard")
grab("dash_full", win, [])

# =========================================================================== 11. копии и безопасность
backups = DATA / "backups" / "local"
create_backup(ctx, backups, kind="manual", create_dir=True)
update_settings(ctx, {"backup.external_path": r"E:\Резервные копии AutoService"})
win.go("settings")
sp.tabs.setCurrentIndex(2)
sp.refresh()
grab("settings_backup", win, [sp.b_ext, btn(sp, "Создать копию сейчас…"), btn(sp, "Восстановить из резервной копии…")])
capture_dialog("backup_now", sp.backup_now, win, hl=lambda d: [field(d, "Куда")])
sp.tabs.setCurrentIndex(5)
sp.load_audit()
grab("audit_log", win, [sp.audit])
sp.tabs.setCurrentIndex(6)
sp.refresh()
grab("settings_ai", win, [sp.llm_on, sp.llm_info, sp.ocr_info])

mgr = auth.login(ctx, "manager", "Manager-pass-1")
win2 = MainWindow(ctx.as_user(mgr))
win2.timer.stop()
win2.resize(W, H)
win2.show()
win2.statusBar().showMessage(f"Данные: {SHOWN_ROOT}")
win2.go("dashboard")
grab("manager_dashboard", win2, [])

(OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("Готово, кадров:", len(meta))
