"""Интерфейс: все 12 разделов и основные диалоги открываются на реальных данных без ошибок."""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from helpers import populate


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def no_popups(monkeypatch):
    shown = []

    def record(kind):
        def _f(parent, title, text, *a, **k):
            shown.append((kind, title, text))
            return QMessageBox.StandardButton.Yes if kind == "question" else QMessageBox.StandardButton.Ok
        return _f

    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(record(kind)))
    return shown


def test_all_pages_and_dialogs(qapp, app, no_popups, tmp_path):
    from autoservice.ai import invoice_import
    from autoservice.ui.dialogs import PartDialog, PartPicker, VehicleDialog
    from autoservice.ui.main_window import MENU, FirstRunDialog, LoginDialog, MainWindow
    from autoservice.ui.pages.purchases import InvoiceReviewDialog, PurchaseEditor
    from autoservice.ui.pages.repairs import VehiclePicker, WorkOrderEditor
    from autoservice.ui.pages.sales import SaleEditor, SaleLineDialog
    from autoservice.ui.pages.stock import InventoryDialog
    from autoservice.ui.pages.vehicles import VehicleCard
    from autoservice.application import catalog, stock_docs

    data = populate(app)
    win = MainWindow(app)
    win.timer.stop()
    for key, _ in MENU:
        win.go(key)
        qapp.processEvents()
    stock = win.pages["stock"]
    for i in range(stock.tabs.count()):
        stock.tabs.setCurrentIndex(i)
        qapp.processEvents()
    reports_page = win.pages["reports"]
    for i in range(reports_page.list.count()):
        reports_page.list.setCurrentRow(i)
        qapp.processEvents()
    assistant = win.pages["assistant"]
    assistant.set_vehicle(data["vehicle"])
    assistant.complaint.setPlainText("горит P0171, плавают обороты")
    assistant.run_basic()
    assert "P0171" in assistant.out.toPlainText()
    dialogs = [
        PartDialog(app, data["oil"]), PartDialog(app, None), PartPicker(app), VehicleDialog(app), VehiclePicker(app),
        VehicleCard(app, data["vehicle"], win.pages["vehicles"]), WorkOrderEditor(app, data["wo"], win.pages["repairs"]),
        SaleEditor(app, data["sale"]), PurchaseEditor(app, data["purchase"]),
        SaleLineDialog(app, catalog.get_part(app, data["filter"])),
        InventoryDialog(app, stock_docs.create_inventory(app, [data["oil"]])),
        LoginDialog(app), FirstRunDialog(app),
    ]
    f = tmp_path / "inv.txt"
    f.write_text("Счёт № 7 от 01.11.2026\n1 90915-YZZE1 Фильтр 2 шт 500,00 1 000,00\nИтого: 1 000,00\n", encoding="utf-8")
    dialogs.append(InvoiceReviewDialog(app, invoice_import.import_invoice(app, f)))
    for d in dialogs:
        d.show()
        qapp.processEvents()
        d.close()
    win.close()
    problems = [p for p in no_popups if p[0] in ("warning", "critical")]
    assert problems == []


def test_manager_sees_restricted_sections(qapp, manager, no_popups):
    from autoservice.ui.main_window import MENU, MainWindow

    win = MainWindow(manager)
    win.timer.stop()
    for key, _ in MENU:
        win.go(key)
        qapp.processEvents()
    win.close()
    assert [p for p in no_popups if p[0] in ("warning", "critical")] == []
