"""Главная панель: показатели, списки внимания, быстрые действия."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QPushButton, QTabWidget, QWidget

from autoservice.application import dashboard
from autoservice.ui.common import DataTable, button, card, date_edit, fmt, page_header, qdate
from autoservice.ui.pages import Page


class DashboardPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.period = QComboBox()
        for text, key in (("Сегодня", "today"), ("Последние 7 дней", "week"), ("Текущий месяц", "month"), ("Произвольный период", "custom")):
            self.period.addItem(text, key)
        self.period.setCurrentIndex(2)
        self.d1, self.d2 = date_edit(date.today().replace(day=1)), date_edit()
        self.period.currentIndexChanged.connect(self._period_changed)
        self._period_changed()
        self.layout_.addWidget(page_header("Главная", "Показатели по проведённым операциям. Пустая база — нулевые значения.",
                                           [self.period, self.d1, self.d2, button("Обновить", self.refresh)]))
        grid = QGridLayout()
        self.cards = {}
        specs = [
            ("revenue_today", "Выручка за сегодня", "money"), ("revenue_period", "Выручка за период", "money"),
            ("gross_profit_period", "Валовая прибыль", "money"), ("expenses_period", "Расходы", "money"),
            ("cash_in_period", "Поступило денег", "money"), ("customer_debts", "Долги клиентов", "money"),
            ("supplier_debts", "Долги поставщикам", "money"), ("cars_in_repair", "Машин в ремонте", "int"),
            ("completed_orders", "Завершено заказов", "int"), ("stock_value", "Себестоимость склада", "money"),
        ]
        for i, (key, title, kind) in enumerate(specs):
            frame, value, hint = card(title)
            self.cards[key] = (value, hint, kind)
            grid.addWidget(frame, i // 5, i % 5)
        host = QWidget()
        host.setLayout(grid)
        self.layout_.addWidget(host)

        quick = QGridLayout()
        actions = [
            ("Новый автомобиль", lambda: self.win.go("vehicles", "new_vehicle")),
            ("Новый заказ-наряд", lambda: self.win.go("repairs", "new")),
            ("Новая закупка", lambda: self.win.go("purchases", "new")),
            ("Продажа запчасти", lambda: self.win.go("sales", "new")),
            ("Сканирование накладной", lambda: self.win.go("purchases", "import")),
            ("Поиск автомобиля", lambda: self.win.go("vehicles", "search")),
            ("Поиск запчасти", lambda: self.win.go("stock", "search")),
            ("Открыть ИИ-помощника", lambda: self.win.go("assistant")),
            ("Финансовый отчёт", lambda: self.win.go("reports", "summary")),
            ("Резервное копирование", lambda: self.win.go("settings", "backup")),
        ]
        for i, (text, slot) in enumerate(actions):
            b = QPushButton(text)
            b.setObjectName("Quick")
            b.clicked.connect(slot)
            quick.addWidget(b, i // 5, i % 5)
        qhost = QWidget()
        qhost.setLayout(quick)
        self.layout_.addWidget(qhost)

        self.tabs = QTabWidget()
        self.low = DataTable([("name", "Запчасть", "text"), ("article", "Артикул", "text"), ("available", "Доступно", "qty"),
                              ("min_stock", "Минимум", "qty"), ("unit", "Ед.", "text")], empty_text="Все остатки в норме")
        self.low.activated.connect(lambda r: self.win.go("stock", "open_part", part_id=r["id"]))
        self.unpaid = DataTable([("date", "Дата", "date"), ("doc", "Документ", "text"), ("customer", "Клиент", "text"),
                                 ("phone", "Телефон", "text"), ("total", "Сумма", "money"), ("paid", "Оплачено", "money"),
                                 ("debt", "Долг", "money"), ("days", "Дней", "text")], empty_text="Неоплаченных документов нет")
        self.review = DataTable([("id", "Импорт №", "text"), ("supplier", "Поставщик", "text"), ("doc_number", "Документ", "text"),
                                 ("created_at", "Загружен", "datetime"), ("warnings", "Предупреждений", "text")],
                                empty_text="Документов на проверке нет")
        self.review.activated.connect(lambda r: self.win.go("purchases", "review", import_id=r["id"]))
        self.recs = DataTable([("part_name", "Запчасть", "text"), ("article", "Артикул", "text"), ("available", "Доступно", "qty"),
                               ("recommended_qty", "Рекомендуется", "qty"), ("supplier", "Поставщик", "text"),
                               ("last_price", "Последняя цена", "money"), ("reliability", "Достоверность", "text"),
                               ("basis", "Основание", "text")], empty_text="Рекомендаций нет")
        self.recs.activated.connect(lambda r: self.win.go("assistant", "recommendations"))
        self.tabs.addTab(self.low, "Заканчивающиеся запчасти")
        self.tabs.addTab(self.unpaid, "Неоплаченные документы")
        self.tabs.addTab(self.review, "Документы на проверку")
        self.tabs.addTab(self.recs, "Рекомендации по закупкам")
        self.layout_.addWidget(self.tabs, 1)

    def _period_changed(self):
        key = self.period.currentData()
        today = date.today()
        if key == "today":
            a, b = today, today
        elif key == "week":
            a, b = today - timedelta(days=6), today
        elif key == "month":
            a, b = today.replace(day=1), today
        else:
            a, b = qdate(self.d1), qdate(self.d2)
        custom = key == "custom"
        self.d1.setEnabled(custom)
        self.d2.setEnabled(custom)
        if not custom:
            from PySide6.QtCore import QDate

            self.d1.setDate(QDate(a.year, a.month, a.day))
            self.d2.setDate(QDate(b.year, b.month, b.day))

    def refresh(self):
        d = dashboard.dashboard(self.ctx, qdate(self.d1), qdate(self.d2))
        for key, (value_lbl, hint, kind) in self.cards.items():
            if key in d:
                value_lbl.setText(fmt(d[key], "money") if kind == "money" else str(d[key]))
                hint.setText("")
            else:
                value_lbl.setText("—")
                hint.setText("Доступно администратору")
        if d.get("cogs_incomplete"):
            self.cards["gross_profit_period"][1].setText(f"Без себестоимости документов: {d['cogs_incomplete']} — прибыль неполная")
        self.low.set_rows(d["low_stock"])
        self.unpaid.set_rows(d["unpaid_docs"])
        self.review.set_rows(d["documents_to_review"])
        self.recs.set_rows(d["purchase_recommendations"])
        self.tabs.setTabText(0, f"Заканчивающиеся запчасти ({len(d['low_stock'])})")
        self.tabs.setTabText(1, f"Неоплаченные документы ({len(d['unpaid_docs'])})")
        self.tabs.setTabText(2, f"Документы на проверку ({len(d['documents_to_review'])})")
        self.tabs.setTabText(3, f"Рекомендации по закупкам ({len(d['purchase_recommendations'])})")


__all__ = ["DashboardPage", "QHBoxLayout"]
