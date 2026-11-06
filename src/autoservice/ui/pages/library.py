"""Библиотека автодела: документы, заметки, поиск, правила диагностики, история ремонтов."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from autoservice.application import catalog
from autoservice.application.documents_store import document_file
from autoservice.domain.enums import Verification
from autoservice.knowledge import library, rules
from autoservice.ui.common import (
    FAILED,
    DataTable,
    FormDialog,
    button,
    combo,
    fmt,
    info,
    label,
    open_path,
    page_header,
    pick_file,
    run,
)
from autoservice.ui.pages import Page

LIB_FILTER = "Документы (*.pdf *.txt *.docx *.xlsx *.csv *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)"


class LibraryPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header(
            "Библиотека автодела", "Собственные материалы автосервиса: руководства, регламенты, процедуры, заметки. "
            "Коммерческие каталоги в поставку не входят — загружайте материалы, на которые есть права.",
            [button("Добавить документ", self.add_doc, primary=True), button("Добавить заметку", self.add_note)]))
        self.tabs = QTabWidget()
        # поиск
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Что ищем: неисправность, узел, код ошибки, регламент…")
        self.make, self.model, self.engine = QLineEdit(), QLineEdit(), QLineEdit()
        self.make.setPlaceholderText("Марка")
        self.model.setPlaceholderText("Модель")
        self.engine.setPlaceholderText("Двигатель")
        cats = catalog.list_categories(ctx)
        self.cat = combo([("Все системы", None)] + [(c["name"], c["id"]) for c in cats])
        for x in (self.q, self.make, self.model, self.engine, self.cat):
            row.addWidget(x, 2 if x is self.q else 1)
        row.addWidget(button("Найти", self.search, primary=True))
        v.addLayout(row)
        self.results = DataTable([("title", "Материал", "text"), ("kind_title", "Тип", "text"), ("make", "Марка", "text"),
                                  ("model", "Модель", "text"), ("category", "Система", "text"), ("status_title", "Статус", "text"),
                                  ("source", "Источник", "text"), ("snippet", "Фрагмент", "text")],
                                 empty_text="Ничего не найдено. Информация отсутствует в локальной библиотеке.")
        self.results.activated.connect(lambda r: self.open_entry(r["id"]))
        self.q.returnPressed.connect(self.search)
        v.addWidget(self.results, 1)
        self.tabs.addTab(w, "Поиск")
        # правила
        w = QWidget()
        v = QVBoxLayout(w)
        self.rules = DataTable([("dtc_code", "Код", "text"), ("title", "Правило", "text"), ("system_code", "Система", "text"),
                                ("status_title", "Статус источника", "text"), ("builtin", "Встроенное", "text"), ("active", "Активно", "text"),
                                ("source", "Источник", "text")], search=True, empty_text="Правил нет")
        self.rules.activated.connect(self.show_rule)
        v.addWidget(self.rules, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новое правило", self.new_rule, primary=True))
        row.addWidget(button("Включить / отключить", self.toggle_rule))
        row.addWidget(button("Импорт правил (JSON)", self.import_rules))
        row.addStretch()
        v.addLayout(row)
        v.addWidget(label("Встроенные правила — общие определения кодов OBD-II и типовые проверки; всегда «Требует проверки».", "Muted"))
        self.tabs.addTab(w, "Правила диагностики")
        # история
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.hq, self.hmake, self.hmodel = QLineEdit(), QLineEdit(), QLineEdit()
        self.hq.setPlaceholderText("Жалоба, код, работа")
        self.hmake.setPlaceholderText("Марка")
        self.hmodel.setPlaceholderText("Модель")
        for x in (self.hq, self.hmake, self.hmodel):
            row.addWidget(x)
        row.addWidget(button("Найти", self.search_history, primary=True))
        v.addLayout(row)
        self.history = DataTable([("number", "Заказ-наряд", "text"), ("closed_at", "Выдан", "date"), ("vehicle", "Автомобиль", "text"),
                                  ("mileage", "Пробег", "text"), ("complaint", "Жалоба", "text"), ("diagnosis", "Диагностика", "text"),
                                  ("services_text", "Работы", "text"), ("parts_text", "Запчасти", "text")],
                                 empty_text="Подтверждённых ремонтов по запросу нет")
        v.addWidget(self.history, 1)
        v.addWidget(label("Подтверждённые ремонты — фактически выполненные и выданные заказ-наряды этого автосервиса.", "Muted"))
        self.tabs.addTab(w, "История подтверждённых ремонтов")
        self.layout_.addWidget(self.tabs, 1)

    def refresh(self):
        self.search()
        self.load_rules()

    def search(self):
        self.results.set_rows(library.search(self.ctx, self.q.text(), self.make.text(), self.model.text(), self.engine.text(), self.cat.currentData()))

    def load_rules(self):
        self.rules.set_rows([{**r, "builtin": "да" if r["is_builtin"] else "", "active": "да" if r["is_active"] else "нет"}
                             for r in rules.list_rules(self.ctx)])

    def search_history(self):
        self.history.set_rows([{**h, "services_text": "; ".join(h["services"]), "parts_text": "; ".join(h["parts"])}
                               for h in library.repair_history(self.ctx, self.hq.text(), self.hmake.text(), self.hmodel.text())])

    def _meta_fields(self, d: FormDialog, kind_default="document"):
        title = d.add("Название*", QLineEdit())
        kind = d.add("Тип", combo([(t, k) for k, t in library.KINDS.items()], kind_default))
        source = d.add("Источник*", QLineEdit())
        source.setPlaceholderText("Издатель, автор, «опыт мастера», договор на использование…")
        make, model, engine = d.add("Марка", QLineEdit()), d.add("Модель", QLineEdit()), d.add("Двигатель", QLineEdit())
        cats = catalog.list_categories(self.ctx)
        cat = d.add("Система автомобиля", combo([("— общая —", None)] + [(c["name"], c["id"]) for c in cats]))
        status = d.add("Статус", combo([(t, k) for k, t in Verification.TITLES.items()], Verification.NEEDS_CHECK))
        return lambda: {"title": title.text(), "kind": kind.currentData(), "source": source.text(), "make": make.text(),
                        "model": model.text(), "engine": engine.text(), "category_id": cat.currentData(),
                        "verification_status": status.currentData()}, title

    def add_doc(self):
        path = pick_file(self, "Документ для библиотеки", LIB_FILTER)
        if not path:
            return
        d = FormDialog("Документ в библиотеку", self, ok_text="Добавить")
        meta, title = self._meta_fields(d)
        title.setText(path.stem)
        res = {}
        d.on_accept = lambda: res.setdefault("r", library.add_document(self.ctx, path, meta()))
        if d.exec():
            _, warnings = res["r"]
            if warnings:
                info(self, "Документ добавлен.\n\n" + "\n".join("• " + w for w in warnings))
            self.refresh()

    def add_note(self):
        d = FormDialog("Заметка мастера", self, ok_text="Сохранить", width=700)
        meta, _ = self._meta_fields(d, "note")
        body = d.add("Текст*", QPlainTextEdit())
        body.setMinimumHeight(180)
        d.on_accept = lambda: library.add_note(self.ctx, meta(), body.toPlainText())
        if d.exec():
            self.refresh()

    def open_entry(self, entry_id):
        e = library.get_entry(self.ctx, entry_id)
        dlg = QDialog(self)
        dlg.setWindowTitle(e["title"])
        dlg.resize(900, 650)
        v = QVBoxLayout(dlg)
        v.addWidget(label(f"{e['kind_title']} · Источник: {e['source']} · Статус: {e['status_title']} · "
                          f"{' '.join(x for x in (e['make'], e['model'], e['engine']) if x) or 'общий материал'} · добавлен {fmt(e['created_at'], 'date')}"))
        text = QTextBrowser()
        text.setPlainText(e["content_text"] or "Текст не извлечён. Откройте оригинал документа.")
        v.addWidget(text, 1)
        row = QHBoxLayout()
        if e["document_id"]:
            row.addWidget(button("Открыть оригинал", lambda: open_path(document_file(self.ctx, e["document_id"]))))
        row.addStretch()
        row.addWidget(button("Закрыть", dlg.accept))
        v.addLayout(row)
        dlg.exec()

    def show_rule(self, r):
        text = (f"{r['title']}\nКод: {r['dtc_code'] or '—'} · Ключевые слова: {r['keywords'] or '—'}\nИсточник: {r['source']} ({r['status_title']})\n\n"
                "Возможные причины (гипотезы):\n" + "\n".join("• " + c for c in r["possible_causes"]) +
                "\n\nПроверки:\n" + "\n".join("• " + c for c in r["checks"]))
        info(self, text, "Правило диагностики")

    def new_rule(self):
        d = FormDialog("Правило диагностики", self, width=700)
        title = d.add("Название*", QLineEdit())
        code = d.add("Код ошибки (если есть)", QLineEdit())
        kw = d.add("Ключевые слова жалобы (через запятую)", QLineEdit())
        make, model = d.add("Только для марки", QLineEdit()), d.add("Только для модели", QLineEdit())
        causes = d.add("Возможные причины (по строке)", QPlainTextEdit())
        checks = d.add("Порядок проверок (по строке)", QPlainTextEdit())
        cats = catalog.list_categories(self.ctx)
        cat = d.add("Категория запчастей", combo([("—", "")] + [(c["name"], c["code"]) for c in cats]))
        critical = d.add("", QCheckBox("Критичная система (безопасность)"))
        source = d.add("Источник*", QLineEdit())
        status = d.add("Статус источника", combo([(t, k) for k, t in Verification.TITLES.items()], Verification.NEEDS_CHECK))
        d.on_accept = lambda: rules.save_rule(self.ctx, {
            "title": title.text(), "dtc_code": code.text(), "keywords": kw.text(), "make": make.text(), "model": model.text(),
            "possible_causes": causes.toPlainText(), "checks": checks.toPlainText(),
            "part_category_codes": [cat.currentData()] if cat.currentData() else [], "is_critical": critical.isChecked(),
            "source": source.text(), "verification_status": status.currentData()})
        if d.exec():
            self.load_rules()

    def toggle_rule(self):
        r = self.rules.selected()
        if r and run(self, rules.set_rule_active, self.ctx, r["id"], not r["is_active"]) is not FAILED:
            self.load_rules()

    def import_rules(self):
        path = pick_file(self, "Файл правил", "JSON (*.json)")
        if path:
            n = run(self, rules.import_rules, self.ctx, path)
            if n is not FAILED:
                info(self, f"Импортировано правил: {n}")
                self.load_rules()
