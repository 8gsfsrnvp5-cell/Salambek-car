"""Сценарии 22–25: PDF, печать, Excel, отчёты."""

from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from autoservice.application import dashboard, reports
from autoservice.application.reports import ReportFilters
from autoservice.application.settings_service import update_settings
from autoservice.documents import excel, generator, printing
from autoservice.domain.errors import PermissionDenied

from helpers import populate


def pdf_text(path):
    return "\n".join(p.extract_text() for p in PdfReader(str(path)).pages)


def test_22_pdf_documents_with_cyrillic(app, tmp_path):
    data = populate(app)
    update_settings(app, {"company.name": "Автосервис «Тест»", "company.inn": "7712345678"})
    for code, (title, fn) in generator.DOCUMENTS_FOR_WORK_ORDER.items():
        path = fn(app, data["wo"])
        assert path.exists() and path.stat().st_size > 1000, code
    text = pdf_text(generator.service_receipt_pdf(app, data["wo"]))
    assert "Квитанция об обслуживании" in text and "Замена моторного масла" in text
    assert "4 850,00" in text.replace(" ", " ")
    assert "Автосервис «Тест»" in text and "ИНН 7712345678" in text
    assert "Не является кассовым чеком" in text
    assert "КПП" not in text  # незаполненные реквизиты не печатаются
    for path in (generator.sale_pdf(app, data["sale"]), generator.internal_waybill_purchase_pdf(app, data["purchase"]),
                 generator.vehicle_report_pdf(app, data["vehicle"]), generator.thermal_receipt_pdf(app, "sale", data["sale"], width_mm=58)):
        assert path.exists() and len(PdfReader(str(path)).pages) >= 1
    vt = pdf_text(generator.vehicle_report_pdf(app, data["vehicle"]))
    assert "А123ВС77" in vt and "Подсос воздуха" in vt


def test_vat_only_when_configured(app):
    data = populate(app)
    assert "НДС" not in pdf_text(generator.act_pdf(app, data["wo"]))
    update_settings(app, {"tax.vat_mode": "included", "tax.vat_rate": "20"})
    text = pdf_text(generator.act_pdf(app, data["wo"])).replace(" ", " ")
    assert "В том числе НДС 20%" in text and "808,33" in text  # 4850 × 20 / 120


def test_23_print_document_through_printer_path(app, tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    data = populate(app)
    src = generator.work_order_pdf(app, data["wo"])
    out = tmp_path / "printed.pdf"
    pages = printing.print_to_pdf_file(src, out)
    assert pages >= 1 and out.exists() and len(PdfReader(str(out)).pages) == pages


def test_24_excel_and_csv_export(app, tmp_path):
    populate(app)
    table = reports.run_report(app, "sales", ReportFilters())
    xlsx = excel.export_xlsx(table, tmp_path / "sales.xlsx")
    ws = load_workbook(xlsx).active
    assert ws["A1"].value == "Продажи за период"
    header = [c.value for c in ws[4]]
    assert "Наименование" in header and "Валовая прибыль" in header
    values = [row for row in ws.iter_rows(min_row=5, values_only=True) if row[0] is not None]
    assert any(v[0] == "Итого" for v in values)
    money_cell = ws.cell(row=5, column=header.index("Цена") + 1)
    assert "₽" in money_cell.number_format and money_cell.value == 650
    csv_path = excel.export_csv(table, tmp_path / "sales.csv")
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and "650,00".encode() in raw


@pytest.mark.parametrize("code", list(reports.REPORTS))
def test_25_all_reports_build_and_export(app, tmp_path, code):
    populate(app)
    table = reports.run_report(app, code, ReportFilters())
    assert table.title
    excel.export_xlsx(table, tmp_path / f"{code}.xlsx")
    generator.report_pdf(app, table, tmp_path / f"{code}.pdf")


def test_financial_summary_numbers(app):
    populate(app)
    t = reports.run_report(app, "summary", ReportFilters())
    vals = {r["name"]: r["value"] for r in t.rows}
    assert vals["Продажи запчастей (за вычетом скидок)"] == Decimal("650.00")
    assert vals["Работы по заказ-нарядам"] == Decimal("1000.00")
    assert vals["Запчасти в заказ-нарядах"] == Decimal("3850.00")
    assert vals["Выручка (нетто)"] == Decimal("5500.00")
    assert vals["Себестоимость итого"] == Decimal("3000.00")  # 500 + 4 × 500 + 500
    assert vals["Валовая прибыль"] == Decimal("2500.00")
    assert vals["Поступило денег"] == Decimal("3650.00")
    assert vals["Долги клиентов (на сегодня)"] == Decimal("1850.00")
    assert vals["Долги перед поставщиками (на сегодня)"] == Decimal("5500.00")
    assert any("Налоговые параметры не настроены" in n for n in t.notes)


def test_empty_database_dashboard_shows_zeros(app):
    d = dashboard.dashboard(app)
    assert d["revenue_today"] == 0 and d["revenue_period"] == 0 and d["stock_value"] == 0
    assert d["cars_in_repair"] == 0 and d["low_stock"] == [] and d["purchase_recommendations"] == []


def test_manager_cannot_open_finance_reports(manager):
    with pytest.raises(PermissionDenied):
        reports.run_report(manager, "summary")
    reports.run_report(manager, "stock")
    assert "summary" not in dict(reports.available_reports(manager))
    assert dashboard.dashboard(manager)["finance_visible"] is False
