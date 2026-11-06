"""Экспорт отчётов в Excel (.xlsx) и CSV для бухгалтера."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from autoservice import APP_NAME
from autoservice.application.reports import ReportTable

MONEY_FMT = '#,##0.00\\ "₽";[Red]-#,##0.00\\ "₽"'
QTY_FMT = "#,##0.###"
PCT_FMT = '0.00"%"'
DATE_FMT = "DD.MM.YYYY"
DATETIME_FMT = "DD.MM.YYYY HH:MM"


def _cell_value(value, kind):
    if value is None:
        return None
    if kind in ("money", "qty", "percent") and isinstance(value, Decimal):
        return value  # openpyxl записывает Decimal как число без потери точности
    return value


def export_xlsx(table: ReportTable, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = table.title[:31]
    bold = Font(bold=True)
    thin = Side(style="thin", color="999999")
    ws.append([table.title])
    ws["A1"].font = Font(bold=True, size=13)
    meta = []
    if table.period:
        meta.append(f"Период: {table.period[0]:%d.%m.%Y} — {table.period[1]:%d.%m.%Y}")
    if table.filters_text:
        meta.append(f"Фильтры: {table.filters_text}")
    meta.append(f"Сформировано: {table.generated_at:%d.%m.%Y %H:%M} ({APP_NAME})")
    ws.append(["; ".join(meta)])
    header_row = 4  # строка 3 — пустая
    for idx, c in enumerate(table.columns, 1):
        ws.cell(row=header_row, column=idx, value=c.title)
    for idx, _ in enumerate(table.columns, 1):
        cell = ws.cell(row=header_row, column=idx)
        cell.font = bold
        cell.fill = PatternFill("solid", fgColor="E8E8E8")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
    r = header_row
    for row in table.rows:
        r += 1
        for idx, c in enumerate(table.columns, 1):
            cell = ws.cell(row=r, column=idx, value=_cell_value(row.get(c.key), c.kind))
            if c.kind == "money":
                cell.number_format = MONEY_FMT
            elif c.kind == "qty":
                cell.number_format = QTY_FMT
            elif c.kind == "percent":
                cell.number_format = PCT_FMT
            elif c.kind == "date":
                cell.number_format = DATE_FMT
            elif c.kind == "datetime":
                cell.number_format = DATETIME_FMT
    last_data = r
    next_row = last_data + 1
    if table.totals and table.rows:
        total_row = last_data + 1
        next_row = total_row + 1
        for idx, c in enumerate(table.columns, 1):
            cell = ws.cell(row=total_row, column=idx)
            if idx == 1:
                cell.value = "Итого"
            if c.key in table.totals and table.totals[c.key] is not None:
                cell.value = table.totals[c.key]
                cell.number_format = MONEY_FMT if c.kind == "money" else QTY_FMT
            cell.font = bold
            cell.border = Border(top=thin)
    for note in table.notes:
        next_row += 1
        ws.cell(row=next_row, column=1, value=note)
    for idx, c in enumerate(table.columns, 1):
        ws.column_dimensions[get_column_letter(idx)].width = max(8, min(60, c.width + 2))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    if table.rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(table.columns))}{last_data}"
    wb.properties.creator = APP_NAME
    wb.save(path)
    return path


def _csv_value(value, kind) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Decimal):
        return format(value, "f").replace(".", ",")
    return str(value)


def export_csv(table: ReportTable, path: Path) -> Path:
    """CSV в кодировке UTF-8 с BOM, разделитель «;», десятичная запятая — открывается в русском Excel."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([c.title for c in table.columns])
        for row in table.rows:
            w.writerow([_csv_value(row.get(c.key), c.kind) for c in table.columns])
        if table.totals and table.rows:
            w.writerow(["Итого" if i == 0 and c.key not in table.totals else _csv_value(table.totals.get(c.key), c.kind)
                        for i, c in enumerate(table.columns)])
    return path
