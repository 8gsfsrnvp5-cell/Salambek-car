"""Документы: накладные на проверке, вложения, сформированные PDF (повторная печать)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from autoservice.ai import invoice_import
from autoservice.application.documents_store import KIND_TITLES, list_documents
from autoservice.documents import printing
from autoservice.documents.fiscal import current_registrar
from autoservice.ui.common import DataTable, button, combo, info, label, open_path, page_header, show_in_folder
from autoservice.ui.pages import Page


class DocumentsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Документы", "Оригиналы накладных и вложения хранятся без изменений. "
                                                        "Печатные формы формируются заново из данных — повторная печать доступна всегда.",
                                           [button("Распознать накладную", lambda: self.win.go("purchases", "import"), primary=True)]))
        self.layout_.addWidget(label(current_registrar().status_text(), "Muted"))
        self.tabs = QTabWidget()
        w = QWidget()
        v = QVBoxLayout(w)
        self.review = DataTable([("id", "№", "text"), ("created_at", "Загружен", "datetime"), ("status_title", "Статус", "text"),
                                 ("supplier_name", "Поставщик", "text"), ("doc_number", "Номер", "text"),
                                 ("total_declared", "Итог документа", "money"), ("lines_count", "Строк", "text")],
                                empty_text="Документов, требующих проверки, нет")
        self.review.activated.connect(lambda r: self.win.go("purchases", "review", import_id=r["id"]))
        v.addWidget(self.review, 1)
        self.tabs.addTab(w, "Требуют проверки")
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.kind = combo([("Все", None)] + [(t, k) for k, t in KIND_TITLES.items()])
        self.q = QLineEdit()
        self.q.setPlaceholderText("Название или имя файла")
        row.addWidget(self.kind)
        row.addWidget(self.q, 1)
        row.addWidget(button("Найти", self.load_files))
        v.addLayout(row)
        self.files = DataTable([("id", "№", "text"), ("kind_title", "Вид", "text"), ("title", "Название", "text"),
                                ("original_name", "Файл", "text"), ("created_at", "Добавлен", "datetime"), ("sha256", "SHA-256", "text")],
                               empty_text="Вложений нет")
        self.files.activated.connect(lambda r: open_path(r["path"]))
        v.addWidget(self.files, 1)
        row = QHBoxLayout()
        row.addWidget(button("Открыть", lambda: self.files.selected() and open_path(self.files.selected()["path"])))
        row.addWidget(button("Показать в папке", lambda: self.files.selected() and show_in_folder(self.files.selected()["path"])))
        row.addStretch()
        v.addLayout(row)
        self.tabs.addTab(w, "Вложения и оригиналы")
        w = QWidget()
        v = QVBoxLayout(w)
        self.generated = DataTable([("name", "Файл", "text"), ("modified", "Сформирован", "datetime"), ("folder", "Папка", "text")],
                                   search=True, empty_text="Документы ещё не формировались")
        self.generated.activated.connect(lambda r: open_path(r["path"]))
        v.addWidget(self.generated, 1)
        row = QHBoxLayout()
        row.addWidget(button("Открыть", lambda: self.generated.selected() and open_path(self.generated.selected()["path"])))
        row.addWidget(button("Печать", self.print_generated, primary=True))
        row.addWidget(button("Показать в папке", lambda: self.generated.selected() and show_in_folder(self.generated.selected()["path"])))
        row.addStretch()
        v.addLayout(row)
        v.addWidget(label("Чтобы распечатать документ с актуальными данными, откройте заказ-наряд или продажу и сформируйте его заново.", "Muted"))
        self.tabs.addTab(w, "Сформированные документы")
        self.layout_.addWidget(self.tabs, 1)

    def load_files(self):
        self.files.set_rows(list_documents(self.ctx, self.kind.currentData(), self.q.text()))

    def refresh(self):
        self.review.set_rows(invoice_import.list_imports(self.ctx, "needs_review"))
        self.load_files()
        base = self.ctx.paths.exports / "documents"
        rows = []
        if base.exists():
            for p in sorted(base.rglob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True)[:1000]:
                rows.append({"name": p.name, "path": str(p), "folder": p.parent.name, "modified": datetime.fromtimestamp(p.stat().st_mtime).replace(microsecond=0)})
        self.generated.set_rows(rows)

    def print_generated(self):
        r = self.generated.selected()
        if not r:
            return info(self, "Выберите документ")
        printing.print_with_preview(Path(r["path"]), self)
