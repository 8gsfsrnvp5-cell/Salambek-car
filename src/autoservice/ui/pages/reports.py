"""Отчёты: выбор отчёта, фильтры, просмотр, экспорт в Excel/CSV/PDF, печать."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QVBoxLayout, QWidget

from autoservice.application import catalog, reports
from autoservice.application.reports import ReportFilters
from autoservice.documents import excel, generator, printing
from autoservice.ui.common import FAILED, DataTable, button, combo, date_edit, fmt, info, label, open_path, page_header, qdate, run, save_file
from autoservice.ui.pages import Page


class ReportsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Отчёты", "Отчёты для руководителя и бухгалтера. Управленческий учёт по проведённым операциям."))
        split = QSplitter()
        self.list = QListWidget()
        self.list.setMaximumWidth(320)
        for code, title in reports.available_reports(ctx):
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.list.addItem(item)
        split.addWidget(self.list)
        right = QWidget()
        rv = QVBoxLayout(right)
        f = QHBoxLayout()
        self.d1, self.d2 = date_edit(date.today().replace(day=1)), date_edit()
        self.supplier = combo([("Все поставщики", None)] + [(s["name"], s["id"]) for s in catalog.list_suppliers(ctx, True)])
        self.category = combo([("Все категории", None)] + [(c["name"], c["id"]) for c in catalog.list_categories(ctx)])
        self.article = QLineEdit()
        self.article.setPlaceholderText("Артикул")
        self.wo = QLineEdit()
        self.wo.setPlaceholderText("№ заказ-наряда")
        for x in (label("Период:"), self.d1, self.d2, self.supplier, self.category, self.article, self.wo):
            f.addWidget(x)
        f.addWidget(button("Сформировать", self.build, primary=True))
        rv.addLayout(f)
        self.title = label("Выберите отчёт слева")
        self.title.setObjectName("PageTitle")
        rv.addWidget(self.title)
        self.table = DataTable([], empty_text="Нет данных за выбранный период")
        rv.addWidget(self.table, 1)
        self.notes = label("", "Muted")
        rv.addWidget(self.notes)
        row = QHBoxLayout()
        for text, slot in (("Excel (.xlsx)", self.to_xlsx), ("CSV", self.to_csv), ("PDF", self.to_pdf), ("Печать", self.print_)):
            row.addWidget(button(text, slot))
        row.addStretch()
        rv.addLayout(row)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        self.layout_.addWidget(split, 1)
        self.list.currentRowChanged.connect(lambda *_: self.build())
        self.current = None

    def filters(self) -> ReportFilters:
        return ReportFilters(qdate(self.d1), qdate(self.d2), self.supplier.currentData(), self.category.currentData(),
                             self.article.text().strip(), self.wo.text().strip())

    def build(self):
        item = self.list.currentItem()
        if not item:
            return
        table = run(self, reports.run_report, self.ctx, item.data(Qt.ItemDataRole.UserRole), self.filters())
        if table is FAILED:
            return
        self.current = table
        self.title.setText(table.title)
        cols = [(c.key, c.title, c.kind) for c in table.columns]
        new = DataTable(cols, empty_text="Нет данных за выбранный период")
        self.table.parentWidget().layout().replaceWidget(self.table, new)
        self.table.deleteLater()
        self.table = new
        rows = list(table.rows)
        if table.totals and rows:
            total = {c.key: table.totals.get(c.key) for c in table.columns if c.key in table.totals}
            total[table.columns[0].key] = "Итого"
            rows.append({**total, "_color": "#1d4ed8"})
        self.table.set_rows(rows)
        self.notes.setText("\n".join(table.notes))

    def _ensure(self):
        if self.current is None:
            info(self, "Сначала сформируйте отчёт")
            return False
        return True

    def to_xlsx(self):
        if self._ensure():
            path = save_file(self, "Экспорт в Excel", f"{self.current.title} {date.today():%Y-%m-%d}.xlsx", "Excel (*.xlsx)")
            if path and run(self, excel.export_xlsx, self.current, path) is not FAILED:
                open_path(path)

    def to_csv(self):
        if self._ensure():
            path = save_file(self, "Экспорт в CSV", f"{self.current.title} {date.today():%Y-%m-%d}.csv", "CSV (*.csv)")
            if path and run(self, excel.export_csv, self.current, path) is not FAILED:
                info(self, f"Сохранено: {path}\nКодировка UTF-8, разделитель «;», десятичная запятая.")

    def to_pdf(self):
        if self._ensure():
            path = save_file(self, "Сохранить PDF", f"{self.current.title} {date.today():%Y-%m-%d}.pdf", "PDF (*.pdf)")
            if path and run(self, generator.report_pdf, self.ctx, self.current, path) is not FAILED:
                open_path(path)

    def print_(self):
        if self._ensure():
            path = run(self, generator.report_pdf, self.ctx, self.current)
            if path is not FAILED:
                printing.print_with_preview(path, self)

    def handle(self, action, **kw):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == action:
                self.list.setCurrentRow(i)
                return
        info(self, "Отчёт недоступен для вашей роли")


__all__ = ["ReportsPage", "fmt"]
