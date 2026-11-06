"""Общие элементы интерфейса: таблицы, поля ввода, выбор наценки, сообщения."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from autoservice.domain.errors import DomainError
from autoservice.domain.markup import MARKUP_PRESETS
from autoservice.domain.money import fmt_money, fmt_percent, fmt_qty

log = logging.getLogger(__name__)


class _Failed:
    def __bool__(self):
        return False


FAILED = _Failed()


def run(parent, fn, *args, success: str | None = None, **kwargs):
    """Выполняет операцию; ошибки бизнес-правил показывает понятным сообщением."""
    try:
        result = fn(*args, **kwargs)
    except DomainError as exc:
        QMessageBox.warning(parent, "Операция не выполнена", str(exc))
        return FAILED
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка в интерфейсе")
        QMessageBox.critical(parent, "Ошибка", f"Непредвиденная ошибка: {exc}\n\nПодробности записаны в журнал программы.")
        return FAILED
    if success:
        notify(parent, success)
    return result


def notify(parent, text: str) -> None:
    win = parent.window() if parent is not None else None
    bar = win.statusBar() if win is not None and hasattr(win, "statusBar") else None
    if bar is not None:
        bar.showMessage(text, 7000)
    else:
        QMessageBox.information(parent, "Готово", text)


def confirm(parent, text: str, title: str = "Подтверждение") -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def ask_text(parent, title: str, label: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip() if ok and text.strip() else None


def info(parent, text: str, title: str = "Информация") -> None:
    QMessageBox.information(parent, title, text)


def open_path(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def show_in_folder(path) -> None:
    p = Path(path)
    if os.name == "nt" and p.exists():
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(p)])  # noqa: S603,S607 — системный проводник
    else:
        open_path(p.parent)


# --------------------------------------------------------------------------- форматирование


def fmt(value, kind: str = "text") -> str:
    if value is None or value == "":
        return "" if kind in ("text", "bool") else "—"
    if kind == "money":
        return fmt_money(value)
    if kind == "qty":
        return fmt_qty(value)
    if kind == "percent":
        return fmt_percent(value)
    if kind == "date":
        return value.strftime("%d.%m.%Y") if isinstance(value, (date, datetime)) else str(value)
    if kind == "datetime":
        return value.strftime("%d.%m.%Y %H:%M") if isinstance(value, datetime) else str(value)
    if kind == "bool":
        return "да" if value else ""
    return str(value)


# --------------------------------------------------------------------------- таблицы


class DictTableModel(QAbstractTableModel):
    def __init__(self, columns: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key, _, kind = self.columns[index.column()]
        value = self.rows[index.row()].get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            return fmt(value, kind)
        if role == Qt.ItemDataRole.UserRole:
            if isinstance(value, Decimal):
                return float(value)  # только для сортировки
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            return value if value is not None else ""
        if role == Qt.ItemDataRole.TextAlignmentRole and kind in ("money", "qty", "percent", "int"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            color = self.rows[index.row()].get("_color")
            if color:
                from PySide6.QtGui import QColor

                return QColor(color)
        return None


class DataTable(QWidget):
    activated = Signal(dict)
    selectionChanged = Signal()

    def __init__(self, columns: list[tuple[str, str, str]], parent=None, search: bool = False, empty_text: str = "Нет данных",
                 multi: bool = False):
        super().__init__(parent)
        self.model = DictTableModel(columns, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # исходный порядок до щелчка
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if multi else QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.view.doubleClicked.connect(self._activated)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selectionChanged.emit())
        self.empty = QLabel(empty_text)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("Muted")
        stack_host = QWidget()
        self.stack = QStackedLayout(stack_host)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if search:
            self.search = QLineEdit()
            self.search.setPlaceholderText("Фильтр по таблице…")
            self.search.textChanged.connect(self.proxy.setFilterFixedString)
            layout.addWidget(self.search)
        layout.addWidget(stack_host)

    def set_rows(self, rows: list[dict]) -> None:
        self.model.set_rows(rows)
        self.stack.setCurrentIndex(0 if rows else 1)
        self.view.resizeColumnsToContents()
        for i in range(self.model.columnCount()):
            if self.view.columnWidth(i) > 380:
                self.view.setColumnWidth(i, 380)

    def rows(self) -> list[dict]:
        return self.model.rows

    def selected(self) -> dict | None:
        idx = self.view.selectionModel().selectedRows()
        if not idx:
            cur = self.view.currentIndex()
            if not cur.isValid():
                return None
            idx = [cur]
        src = self.proxy.mapToSource(idx[0])
        return self.model.rows[src.row()] if src.isValid() else None

    def selected_rows(self) -> list[dict]:
        out = []
        for idx in self.view.selectionModel().selectedRows():
            src = self.proxy.mapToSource(idx)
            out.append(self.model.rows[src.row()])
        return out

    def _activated(self, index):
        src = self.proxy.mapToSource(index)
        if src.isValid():
            self.activated.emit(self.model.rows[src.row()])


# --------------------------------------------------------------------------- поля ввода


class MoneyEdit(QLineEdit):
    def __init__(self, value=None, parent=None, placeholder: str = "0,00"):
        super().__init__(parent)
        from PySide6.QtCore import QRegularExpression

        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,3}(?:[  ]?\d{3}){0,4}(?:[.,]\d{0,2})?$|^\d{0,12}(?:[.,]\d{0,2})?$"), self))
        self.setPlaceholderText(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        if value is not None:
            self.set_value(value)

    def set_value(self, value) -> None:
        self.setText("" if value is None else format(Decimal(value), "f").replace(".", ","))

    def value(self) -> str | None:
        t = self.text().strip().replace(" ", "").replace(" ", "")
        return t.replace(",", ".") if t else None


class QtyEdit(QLineEdit):
    def __init__(self, value="1", parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QRegularExpression

        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,9}(?:[.,]\d{0,3})?$"), self))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setText(str(value).replace(".", ",") if value is not None else "")

    def value(self) -> str | None:
        t = self.text().strip()
        return t.replace(",", ".") if t else None


class MarkupSelector(QWidget):
    """Наценка 0–100 %: быстрый выбор 0, 10 … 100 % или любое значение с точностью до 0,01 %."""

    changed = Signal()
    AUTO = "auto"
    CUSTOM = "custom"

    def __init__(self, parent=None, allow_auto: bool = True, auto_text: str = "По правилам"):
        super().__init__(parent)
        self.combo = QComboBox()
        if allow_auto:
            self.combo.addItem(auto_text, self.AUTO)
        for p in MARKUP_PRESETS:
            self.combo.addItem(f"{p}%", str(p))
        self.combo.addItem("Другое…", self.CUSTOM)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(0, 100)
        self.spin.setDecimals(2)
        self.spin.setSuffix(" %")
        self.spin.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo)
        lay.addWidget(self.spin)
        self.combo.currentIndexChanged.connect(self._on_combo)
        self.spin.valueChanged.connect(lambda *_: self.changed.emit())

    def _on_combo(self, *_):
        self.spin.setVisible(self.combo.currentData() == self.CUSTOM)
        self.changed.emit()

    def value(self) -> str | None:
        data = self.combo.currentData()
        if data == self.AUTO:
            return None
        if data == self.CUSTOM:
            return f"{self.spin.value():.2f}"
        return data

    def set_value(self, value) -> None:
        if value in (None, ""):
            idx = self.combo.findData(self.AUTO)
            self.combo.setCurrentIndex(max(idx, 0))
            return
        d = Decimal(str(value))
        for i in range(self.combo.count()):
            if self.combo.itemData(i) not in (self.AUTO, self.CUSTOM) and Decimal(self.combo.itemData(i)) == d:
                self.combo.setCurrentIndex(i)
                return
        self.combo.setCurrentIndex(self.combo.findData(self.CUSTOM))
        self.spin.setValue(float(d))


def date_edit(value: date | None = None) -> QDateEdit:
    w = QDateEdit()
    w.setCalendarPopup(True)
    w.setDisplayFormat("dd.MM.yyyy")
    d = value or date.today()
    w.setDate(QDate(d.year, d.month, d.day))
    return w


def qdate(w: QDateEdit) -> date:
    d = w.date()
    return date(d.year(), d.month(), d.day())


def combo(items: list[tuple[str, object]], current=None, editable: bool = False) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for text, data in items:
        c.addItem(text, data)
    if current is not None:
        idx = c.findData(current)
        if idx >= 0:
            c.setCurrentIndex(idx)
    if editable:
        from PySide6.QtWidgets import QCompleter

        c.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        c.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    return c


def combo_value(c: QComboBox):
    if c.isEditable():
        idx = c.findText(c.currentText())
        return c.itemData(idx) if idx >= 0 else None
    return c.currentData()


# --------------------------------------------------------------------------- компоновка


def page_header(title: str, subtitle: str = "", buttons: list[QWidget] | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    box = QVBoxLayout()
    t = QLabel(title)
    t.setObjectName("PageTitle")
    box.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        box.addWidget(s)
    lay.addLayout(box, 1)
    for b in buttons or []:
        lay.addWidget(b)
    return w


def button(text: str, slot=None, primary: bool = False, danger: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    if danger:
        b.setObjectName("Danger")
    if tooltip:
        b.setToolTip(tooltip)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def card(title: str, value: str = "—", hint: str = "") -> tuple[QFrame, QLabel, QLabel]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    t = QLabel(title)
    t.setObjectName("CardTitle")
    v = QLabel(value)
    v.setObjectName("CardValue")
    h = QLabel(hint)
    h.setObjectName("Muted")
    h.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(v)
    lay.addWidget(h)
    return f, v, h


def label(text: str, kind: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if kind:
        lbl.setObjectName(kind)
    return lbl


class FormDialog(QDialog):
    """Простой диалог с формой и кнопками «Сохранить»/«Отмена»."""

    def __init__(self, title: str, parent=None, ok_text: str = "Сохранить", width: int = 520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.body = QVBoxLayout(self)
        self.body.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        self.body.addWidget(self.buttons)
        self.on_accept = None

    def add(self, label_text: str, widget: QWidget) -> QWidget:
        self.form.addRow(label_text, widget)
        return widget

    def _try_accept(self):
        if self.on_accept is None:
            self.accept()
            return
        if run(self, self.on_accept) is not FAILED:
            self.accept()


def pick_file(parent, title: str, filters: str) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, title, str(Path.home()), filters)
    return Path(path) if path else None


def save_file(parent, title: str, default_name: str, filters: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, str(Path.home() / "Documents" / default_name), filters)
    return Path(path) if path else None


def pick_dir(parent, title: str) -> Path | None:
    path = QFileDialog.getExistingDirectory(parent, title, str(Path.home()))
    return Path(path) if path else None


def debounce(widget: QWidget, slot, ms: int = 300) -> QTimer:
    t = QTimer(widget)
    t.setSingleShot(True)
    t.setInterval(ms)
    t.timeout.connect(slot)
    return t
