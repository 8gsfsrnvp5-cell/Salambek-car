"""Главное окно: боковое меню из 12 разделов, страницы, планировщик копий."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from autoservice import APP_NAME, __version__
from autoservice.application import auth
from autoservice.application.context import AppContext
from autoservice.application.settings_service import read_settings, update_settings
from autoservice.backup import scheduler
from autoservice.domain.enums import Role
from autoservice.ui.common import FAILED, MarkupSelector, button, label, run

log = logging.getLogger(__name__)

MENU = [
    ("dashboard", "Главная"),
    ("stock", "Склад"),
    ("purchases", "Закупки"),
    ("sales", "Продажи"),
    ("vehicles", "Автомобили"),
    ("repairs", "Ремонты"),
    ("library", "Библиотека автодела"),
    ("assistant", "ИИ-помощник"),
    ("finance", "Финансы"),
    ("reports", "Отчёты"),
    ("documents", "Документы"),
    ("settings", "Настройки"),
]


class _Bridge(QObject):
    tick_done = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 860)
        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setCentralWidget(root)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(230)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 10)
        sv.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        sv.addWidget(title)
        company = (read_settings(ctx).get("company.name") or "").strip()
        self.subtitle = QLabel(company or "Управление автосервисом")
        self.subtitle.setObjectName("AppSubtitle")
        self.subtitle.setWordWrap(True)
        sv.addWidget(self.subtitle)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav: dict[str, QPushButton] = {}
        for key, text in MENU:
            b = QPushButton(text)
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.group.addButton(b)
            self.nav[key] = b
            sv.addWidget(b)
        sv.addStretch()
        user = QLabel(f"{ctx.user.full_name or ctx.user.username}\n{Role.TITLES.get(ctx.user.role, ctx.user.role)}")
        user.setStyleSheet("color:#d1d5db; padding: 8px 14px;")
        sv.addWidget(user)
        logout = QPushButton("Сменить пользователя")
        logout.setObjectName("NavButton")
        logout.clicked.connect(self.logout)
        sv.addWidget(logout)
        lay.addWidget(side)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        self._build_pages()
        self.statusBar().showMessage(f"Данные: {ctx.paths.root}")
        self.bridge = _Bridge()
        self.bridge.tick_done.connect(self._tick_done)
        self._tick_running = False
        self._shown_warnings: set[str] = set()
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.run_scheduler)
        self.timer.start()
        QTimer.singleShot(5000, self.run_scheduler)
        self.relogin = False
        self.go("dashboard")

    def _build_pages(self):
        from autoservice.ui.pages.assistant import AssistantPage
        from autoservice.ui.pages.dashboard import DashboardPage
        from autoservice.ui.pages.documents import DocumentsPage
        from autoservice.ui.pages.finance import FinancePage
        from autoservice.ui.pages.library import LibraryPage
        from autoservice.ui.pages.purchases import PurchasesPage
        from autoservice.ui.pages.repairs import RepairsPage
        from autoservice.ui.pages.reports import ReportsPage
        from autoservice.ui.pages.sales import SalesPage
        from autoservice.ui.pages.settings import SettingsPage
        from autoservice.ui.pages.stock import StockPage
        from autoservice.ui.pages.vehicles import VehiclesPage

        classes = {"dashboard": DashboardPage, "stock": StockPage, "purchases": PurchasesPage, "sales": SalesPage,
                   "vehicles": VehiclesPage, "repairs": RepairsPage, "library": LibraryPage, "assistant": AssistantPage,
                   "finance": FinancePage, "reports": ReportsPage, "documents": DocumentsPage, "settings": SettingsPage}
        for key, cls in classes.items():
            page = cls(self.ctx, self)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages[key] = page
            self.stack.addWidget(scroll)

    def go(self, key: str, action: str | None = None, **kwargs):
        page = self.pages[key]
        self.nav[key].setChecked(True)
        self.stack.setCurrentWidget(page.parentWidget().parentWidget())
        run(self, page.refresh)
        if action:
            QTimer.singleShot(0, lambda: run(self, page.handle, action, **kwargs))

    def refresh_current(self):
        for key, b in self.nav.items():
            if b.isChecked():
                run(self, self.pages[key].refresh)

    # ------------------------------------------------------------------ расписание копий
    def run_scheduler(self):
        if self._tick_running:
            return
        self._tick_running = True

        def work():
            try:
                res = scheduler.tick(self.ctx)
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка планировщика копий")
                res = scheduler.TickResult(warnings=[f"Ошибка планировщика копий: {exc}"])
            self.bridge.tick_done.emit(res)

        threading.Thread(target=work, name="backup-scheduler", daemon=True).start()

    def _tick_done(self, res):
        self._tick_running = False
        if res.done:
            self.statusBar().showMessage(" · ".join(res.done) + f" ({datetime.now():%H:%M})", 15000)
        today = datetime.now().strftime("%Y-%m-%d")
        for w in res.warnings:
            key = f"{today}:{w[:60]}"
            if key not in self._shown_warnings:
                self._shown_warnings.add(key)
                QMessageBox.warning(self, "Резервное копирование", w)
        for r in res.reminders:
            QMessageBox.information(self, "Резервное копирование", r)

    def logout(self):
        self.relogin = True
        self.close()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


class LoginDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.user = None
        self.setWindowTitle(f"{APP_NAME} — вход")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        t = QLabel(APP_NAME)
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Пользователь", self.username)
        form.addRow("Пароль", self.password)
        lay.addLayout(form)
        self.error = label("", "Danger")
        lay.addWidget(self.error)
        b = button("Войти", self.do_login, primary=True)
        b.setDefault(True)
        lay.addWidget(b)
        self.password.returnPressed.connect(self.do_login)

    def do_login(self):
        try:
            self.user = auth.login(self.ctx, self.username.text(), self.password.text())
        except Exception as exc:  # noqa: BLE001
            self.error.setText(str(exc))
            self.password.clear()
            return
        self.accept()


class FirstRunDialog(QDialog):
    """Первый запуск: создание администратора и минимальные настройки. Ничего не выдумываем."""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.user = None
        self.setWindowTitle(f"{APP_NAME} — первый запуск")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        t = QLabel("Добро пожаловать в AutoService AI")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(label(f"База данных создана: {ctx.paths.db_file}\nСоздайте учётную запись администратора. "
                            "Пароль хранится только в виде защищённого хеша — запишите его в надёжном месте.", "Muted"))
        form = QFormLayout()
        self.company = QLineEdit()
        self.company.setPlaceholderText("Например: Автосервис «Мотор» (можно заполнить позже)")
        self.username = QLineEdit("admin")
        self.full_name = QLineEdit()
        self.p1 = QLineEdit()
        self.p1.setEchoMode(QLineEdit.EchoMode.Password)
        self.p2 = QLineEdit()
        self.p2.setEchoMode(QLineEdit.EchoMode.Password)
        self.markup = MarkupSelector(allow_auto=True, auto_text="Не задавать сейчас")
        self.manager = QCheckBox("Создать также учётную запись менеджера")
        self.m_user = QLineEdit("manager")
        self.m_pass = QLineEdit()
        self.m_pass.setEchoMode(QLineEdit.EchoMode.Password)
        for text, w in (("Название автосервиса", self.company), ("Имя пользователя*", self.username), ("ФИО", self.full_name),
                        ("Пароль* (от 8 символов)", self.p1), ("Пароль ещё раз*", self.p2), ("Стандартная наценка", self.markup),
                        ("", self.manager), ("Имя менеджера", self.m_user), ("Пароль менеджера", self.m_pass)):
            form.addRow(text, w)
        lay.addLayout(form)
        self.error = label("", "Danger")
        lay.addWidget(self.error)
        lay.addWidget(button("Начать работу", self.create, primary=True))

    def create(self):
        if self.p1.text() != self.p2.text():
            self.error.setText("Пароли не совпадают")
            return
        try:
            user = self.user or auth.create_first_admin(self.ctx, self.username.text(), self.p1.text(), self.full_name.text())
            self.user = user  # при повторной попытке администратор не создаётся заново
            ctx = self.ctx.as_user(user)
            values = {}
            if self.company.text().strip():
                values["company.name"] = self.company.text().strip()
            if self.markup.value() is not None:
                values["pricing.default_markup"] = self.markup.value()
            if values:
                update_settings(ctx, values)
            if self.manager.isChecked():
                auth.create_user(ctx, self.m_user.text(), self.m_pass.text(), Role.MANAGER, "Менеджер")
        except Exception as exc:  # noqa: BLE001
            self.error.setText(str(exc))
            return
        self.user = user
        self.accept()


__all__ = ["MainWindow", "LoginDialog", "FirstRunDialog", "FAILED"]
