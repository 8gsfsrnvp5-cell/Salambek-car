"""Настройки: реквизиты, цены и налоги, резервные копии, пользователи, сотрудники, журнал, ИИ и OCR, о программе."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from autoservice import APP_NAME, __version__
from autoservice.ai import llm
from autoservice.ai.invoice_import import ocr_status
from autoservice.application import auth
from autoservice.application.audit_service import list_audit
from autoservice.application.finance import list_employees
from autoservice.application.settings_service import TAX_REGIMES, VAT_MODES, read_settings, set_backup_password, update_settings
from autoservice.backup import dpapi
from autoservice.backup.service import create_backup, list_history, record_verify, restore_backup, verify_backup
from autoservice.backup import crypto
from autoservice.domain.enums import Role
from autoservice.persistence.migrate import current_revision
from autoservice.security.permissions import Perm
from autoservice.ui.common import (
    FAILED,
    DataTable,
    FormDialog,
    MarkupSelector,
    MoneyEdit,
    button,
    combo,
    confirm,
    fmt,
    info,
    label,
    open_path,
    page_header,
    pick_dir,
    pick_file,
    run,
)
from autoservice.ui.dialogs import employee_dialog
from autoservice.ui.pages import Page


class SettingsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win)
        self.layout_.addWidget(page_header("Настройки", "Реквизиты печатаются только заполненные. Налоги не рассчитываются, пока не настроены."))
        self.tabs = QTabWidget()
        self.layout_.addWidget(self.tabs, 1)
        self._company()
        self._pricing()
        self._backup()
        self._users()
        self._employees()
        self._audit()
        self._ai()
        self._about()

    def _form_tab(self, title):
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        v.addLayout(form)
        self.tabs.addTab(w, title)
        return w, v, form

    # ------------------------------------------------------------------ реквизиты
    def _company(self):
        w, v, form = self._form_tab("Автосервис")
        self.company = {}
        for key, title in (("company.name", "Название (в шапке документов)"), ("company.legal_name", "Юридическое наименование"),
                           ("company.inn", "ИНН"), ("company.kpp", "КПП"), ("company.ogrn", "ОГРН / ОГРНИП"), ("company.address", "Адрес"),
                           ("company.phone", "Телефон"), ("company.email", "Эл. почта"), ("company.bank_details", "Банковские реквизиты"),
                           ("company.document_footer", "Подпись внизу документов")):
            self.company[key] = QLineEdit()
            form.addRow(title, self.company[key])
        self.logo_lbl = label("", "Muted")
        row = QHBoxLayout()
        row.addWidget(self.logo_lbl, 1)
        row.addWidget(button("Выбрать логотип…", self.choose_logo))
        row.addWidget(button("Убрать логотип", self.clear_logo))
        lw = QWidget()
        lw.setLayout(row)
        form.addRow("Логотип", lw)
        v.addWidget(button("Сохранить реквизиты", self.save_company, primary=True))
        v.addStretch()

    def save_company(self):
        if run(self, update_settings, self.ctx, {k: w.text().strip() for k, w in self.company.items()}, success="Реквизиты сохранены") is not FAILED:
            self.win.subtitle.setText(self.company["company.name"].text().strip() or "Управление автосервисом")

    def choose_logo(self):
        src = pick_file(self, "Логотип", "Изображения (*.png *.jpg *.jpeg)")
        if not src:
            return
        dest = self.ctx.paths.attachments / "logo" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if run(self, update_settings, self.ctx, {"company.logo_path": str(dest)}) is not FAILED:
            self.refresh()

    def clear_logo(self):
        if run(self, update_settings, self.ctx, {"company.logo_path": ""}) is not FAILED:
            self.refresh()

    # ------------------------------------------------------------------ цены и налоги
    def _pricing(self):
        w, v, form = self._form_tab("Цены и налоги")
        self.default_markup = MarkupSelector(allow_auto=True, auto_text="Не задана")
        form.addRow("Стандартная наценка", self.default_markup)
        form.addRow("", label("Приоритет: наценка в операции → наценка партии → наценка запчасти → стандартная. "
                              "Диапазон 0–100 % от себестоимости.", "Muted"))
        self.labor_rate = MoneyEdit(placeholder="не задана")
        form.addRow("Стоимость нормо-часа", self.labor_rate)
        self.tax_regime = combo([(t or "Не указан", t) for t in TAX_REGIMES])
        form.addRow("Налоговый режим", self.tax_regime)
        self.vat_mode = combo([(t, k) for k, t in VAT_MODES.items()])
        form.addRow("НДС в документах", self.vat_mode)
        self.vat_rate = QLineEdit()
        self.vat_rate.setPlaceholderText("например, 20")
        form.addRow("Ставка НДС, %", self.vat_rate)
        self.receipt_width = combo([("80 мм", 80), ("58 мм", 58)])
        form.addRow("Ширина чековой ленты", self.receipt_width)
        form.addRow("", label("Программа не рассчитывает налоги и не формирует бухгалтерские проводки. Строка «в т.ч. НДС» "
                              "печатается только при режиме «НДС включён в цену» и указанной ставке.", "Muted"))
        v.addWidget(button("Сохранить", self.save_pricing, primary=True))
        v.addStretch()

    def save_pricing(self):
        values = {"pricing.labor_rate": self.labor_rate.value() or "", "tax.regime": self.tax_regime.currentData(),
                  "tax.vat_mode": self.vat_mode.currentData(), "tax.vat_rate": self.vat_rate.text().replace(",", ".").strip() or "",
                  "print.receipt_width_mm": self.receipt_width.currentData()}
        if self.ctx.can(Perm.MARKUP_DEFAULT):
            values["pricing.default_markup"] = self.default_markup.value() or ""
        run(self, update_settings, self.ctx, values, success="Настройки цен и налогов сохранены")

    # ------------------------------------------------------------------ резервные копии
    def _backup(self):
        w, v, form = self._form_tab("Резервное копирование")
        self.b_interval = QSpinBox()
        self.b_interval.setRange(1, 24)
        self.b_interval.setSuffix(" ч")
        self.b_keep = QSpinBox()
        self.b_keep.setRange(1, 500)
        self.b_ext = QLineEdit()
        ext_row = QHBoxLayout()
        ext_row.addWidget(self.b_ext, 1)
        ext_row.addWidget(button("Выбрать папку на внешнем диске…", lambda: self._pick_ext()))
        ew = QWidget()
        ew.setLayout(ext_row)
        self.b_ext_keep = QSpinBox()
        self.b_ext_keep.setRange(1, 1000)
        self.b_daily = QCheckBox("Ежедневная копия на внешний диск")
        self.b_weekly = QCheckBox("Еженедельная проверка целостности копии")
        self.b_monthly = QCheckBox("Ежемесячное напоминание о дополнительной внешней копии")
        self.b_minfree = QSpinBox()
        self.b_minfree.setRange(50, 100000)
        self.b_minfree.setSuffix(" МБ")
        self.enc_lbl = label("", "Muted")
        for text, x in (("Локальный снимок каждые", self.b_interval), ("Хранить локальных снимков", self.b_keep), ("Внешний диск / папка", ew),
                        ("Хранить внешних копий", self.b_ext_keep), ("", self.b_daily), ("", self.b_weekly), ("", self.b_monthly),
                        ("Минимум свободного места", self.b_minfree), ("Шифрование", self.enc_lbl)):
            form.addRow(text, x)
        form.addRow("", label("Копирование по расписанию выполняется, пока программа запущена. Пропущенные копии делаются при следующем запуске.", "Muted"))
        row = QHBoxLayout()
        self.backup_buttons = [button("Сохранить настройки", self.save_backup, primary=True), button("Задать пароль шифрования…", self.set_password),
                               button("Отключить шифрование", lambda: run(self, set_backup_password, self.ctx, None) is not FAILED and self.refresh())]
        for b in self.backup_buttons:
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("Создать копию сейчас…", self.backup_now, primary=True))
        row.addWidget(button("Проверить копию…", self.verify_file))
        self.b_restore = button("Восстановить из резервной копии…", self.restore, danger=True)
        row.addWidget(self.b_restore)
        row.addWidget(button("Открыть папку копий", lambda: open_path(self.ctx.paths.backups)))
        row.addStretch()
        v.addLayout(row)
        self.history = DataTable([("ts", "Дата", "datetime"), ("kind", "Вид", "text"), ("status", "Статус", "text"), ("path", "Файл", "text"),
                                  ("size_mb", "Размер, МБ", "text"), ("encrypted", "Зашифрована", "bool"), ("verify_status", "Проверка", "text"),
                                  ("message", "Сообщение", "text")], empty_text="Копий ещё не было")
        v.addWidget(self.history, 1)

    def _pick_ext(self):
        d = pick_dir(self, "Папка для резервных копий (внешний диск)")
        if d:
            self.b_ext.setText(str(d))

    def save_backup(self):
        run(self, update_settings, self.ctx, {
            "backup.local_interval_hours": self.b_interval.value(), "backup.local_keep": self.b_keep.value(),
            "backup.external_path": self.b_ext.text().strip(), "backup.external_keep": self.b_ext_keep.value(),
            "backup.external_daily": self.b_daily.isChecked(), "backup.verify_weekly": self.b_weekly.isChecked(),
            "backup.monthly_reminder": self.b_monthly.isChecked(), "backup.min_free_mb": self.b_minfree.value()}, success="Сохранено")

    def set_password(self):
        if not dpapi.available():
            return info(self, "Защищённое хранение пароля доступно только в Windows")
        d = FormDialog("Пароль шифрования копий", self)
        d.add("", label("Пароль понадобится для восстановления на другом компьютере. Если его потерять, зашифрованные копии "
                        "восстановить НЕВОЗМОЖНО. Запишите пароль и храните отдельно от компьютера.", "Warning"))
        p1, p2 = d.add("Пароль*", QLineEdit()), d.add("Ещё раз*", QLineEdit())
        for p in (p1, p2):
            p.setEchoMode(QLineEdit.EchoMode.Password)

        def save():
            if p1.text() != p2.text():
                from autoservice.domain.errors import ValidationError

                raise ValidationError("Пароли не совпадают")
            set_backup_password(self.ctx, p1.text())

        d.on_accept = save
        if d.exec():
            self.refresh()

    def backup_now(self):
        s = read_settings(self.ctx)
        options = [("Локальная папка программы", str(self.ctx.paths.local_backups))]
        if s.get("backup.external_path"):
            options.append((f"Внешний диск: {s['backup.external_path']}", s["backup.external_path"]))
        options.append(("Другая папка…", None))
        d = FormDialog("Резервная копия", self, ok_text="Создать")
        target = d.add("Куда", combo(options))
        enc = d.add("", QCheckBox("Зашифровать паролем"))
        pw = d.add("Пароль", QLineEdit())
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        res = {}

        def do():
            dest = target.currentData()
            if dest is None:
                chosen = pick_dir(self, "Папка для копии")
                if not chosen:
                    from autoservice.domain.errors import ValidationError

                    raise ValidationError("Папка не выбрана")
                dest = str(chosen)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                res["info"] = create_backup(self.ctx, Path(dest), "manual", pw.text() if enc.isChecked() else None,
                                            create_dir=dest == str(self.ctx.paths.local_backups))
            finally:
                QApplication.restoreOverrideCursor()

        d.on_accept = do
        if d.exec():
            i = res["info"]
            info(self, f"Копия создана:\n{i['path']}\nРазмер: {i['size'] / 1024 / 1024:.1f} МБ\nSHA-256: {i['sha256']}")
            self.refresh()

    def _ask_password_if_needed(self, path: Path) -> str | None | bool:
        if not crypto.is_encrypted(path):
            return None
        d = FormDialog("Зашифрованная копия", self, ok_text="Продолжить")
        pw = d.add("Пароль копии", QLineEdit())
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        return pw.text() if d.exec() else False

    def verify_file(self):
        path = pick_file(self, "Файл резервной копии", "Копии AutoService AI (*.asbak *.enc)")
        if not path:
            return
        pw = self._ask_password_if_needed(path)
        if pw is False:
            return
        res = verify_backup(path, pw, self.ctx.paths.tmp)
        record_verify(self.ctx, path, res)
        info(self, ("✔ Копия исправна.\n\n" if res.ok else "✖ Копия НЕ прошла проверку.\n\n") + "\n".join(res.messages), "Проверка копии")
        self.refresh()

    def restore(self):
        if not self.ctx.can(Perm.BACKUP_RESTORE):
            return info(self, "Восстановление доступно только администратору")
        path = pick_file(self, "Резервная копия для восстановления", "Копии AutoService AI (*.asbak *.enc)")
        if not path:
            return
        pw = self._ask_password_if_needed(path)
        if pw is False:
            return
        res = verify_backup(path, pw, self.ctx.paths.tmp)
        if not res.ok:
            return info(self, "Копия не прошла проверку — восстановление невозможно:\n" + "\n".join(res.messages))
        m = res.manifest
        d = FormDialog("Подтверждение восстановления", self, ok_text="Восстановить")
        d.add("", label(f"Копия от {m['created_at']} (компьютер {m['computer']}, версия программы {m['app_version']}, схема {m['schema_revision']}).\n"
                        "ВСЕ текущие данные будут заменены данными из копии. Текущая база будет сохранена отдельной копией.", "Warning"))
        admin_pw = d.add("Пароль администратора*", QLineEdit())
        admin_pw.setEchoMode(QLineEdit.EchoMode.Password)

        def check():
            if not auth.verify_current_password(self.ctx, admin_pw.text()):
                from autoservice.domain.errors import AuthError

                raise AuthError("Пароль администратора неверный")

        d.on_accept = check
        if not d.exec():
            return
        out = run(self, restore_backup, self.ctx, path, pw)
        if out is FAILED:
            return
        info(self, f"Данные восстановлены. Сохранена копия прежней базы: {out['safety_copy']}\n"
                   f"Восстановлено вложений: {out['attachments']}.\n\nПрограмма будет перезапущена — войдите снова.")
        self.win.relogin = True
        self.win.close()

    # ------------------------------------------------------------------ пользователи и сотрудники
    def _users(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.users = DataTable([("username", "Пользователь", "text"), ("full_name", "ФИО", "text"), ("role_title", "Роль", "text"),
                                ("is_active", "Активен", "bool"), ("last_login_at", "Последний вход", "datetime")], empty_text="")
        v.addWidget(self.users, 1)
        row = QHBoxLayout()
        self.user_buttons = [button("Новый пользователь", self.new_user, primary=True), button("Включить / отключить", self.toggle_user),
                             button("Сбросить пароль", self.reset_password)]
        for b in self.user_buttons:
            row.addWidget(b)
        row.addWidget(button("Сменить мой пароль", self.change_my_password))
        row.addStretch()
        v.addLayout(row)
        self.tabs.addTab(w, "Пользователи")

    def new_user(self):
        d = FormDialog("Новый пользователь", self)
        name, full = d.add("Имя для входа*", QLineEdit()), d.add("ФИО", QLineEdit())
        role = d.add("Роль", combo([(t, k) for k, t in Role.TITLES.items()], Role.MANAGER))
        pw = d.add("Пароль* (от 8 символов)", QLineEdit())
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        d.on_accept = lambda: auth.create_user(self.ctx, name.text(), pw.text(), role.currentData(), full.text())
        if d.exec():
            self.refresh()

    def toggle_user(self):
        r = self.users.selected()
        if r and run(self, auth.set_user_active, self.ctx, r["id"], not r["is_active"]) is not FAILED:
            self.refresh()

    def reset_password(self):
        r = self.users.selected()
        if not r:
            return info(self, "Выберите пользователя")
        d = FormDialog(f"Новый пароль для {r['username']}", self)
        pw = d.add("Пароль*", QLineEdit())
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        d.on_accept = lambda: auth.change_password(self.ctx, r["id"], pw.text())
        d.exec()

    def change_my_password(self):
        d = FormDialog("Смена пароля", self)
        old, new = d.add("Текущий пароль*", QLineEdit()), d.add("Новый пароль*", QLineEdit())
        for x in (old, new):
            x.setEchoMode(QLineEdit.EchoMode.Password)
        d.on_accept = lambda: auth.change_password(self.ctx, self.ctx.user.id, new.text(), old.text())
        if d.exec():
            info(self, "Пароль изменён")

    def _employees(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.emps = DataTable([("full_name", "ФИО", "text"), ("position", "Должность", "text"), ("phone", "Телефон", "text"),
                               ("is_active", "Работает", "bool")], empty_text="Сотрудники не добавлены")
        self.emps.activated.connect(lambda r: employee_dialog(self.ctx, self, r) and self.refresh())
        v.addWidget(self.emps, 1)
        row = QHBoxLayout()
        row.addWidget(button("Новый сотрудник", lambda: employee_dialog(self.ctx, self) and self.refresh(), primary=True))
        row.addWidget(button("Изменить", lambda: self.emps.selected() and employee_dialog(self.ctx, self, self.emps.selected()) and self.refresh()))
        row.addStretch()
        v.addLayout(row)
        v.addWidget(label("Механики привязываются к выполненным работам в заказ-нарядах. Отдельный вход механика — в следующих версиях.", "Muted"))
        self.tabs.addTab(w, "Сотрудники")

    def _audit(self):
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.audit_q = QLineEdit()
        self.audit_q.setPlaceholderText("Действие, пользователь или объект")
        row.addWidget(self.audit_q, 1)
        row.addWidget(button("Показать", self.load_audit))
        v.addLayout(row)
        self.audit = DataTable([("ts", "Время", "datetime"), ("username", "Пользователь", "text"), ("action", "Действие", "text"),
                                ("entity", "Объект", "text"), ("entity_id", "№", "text"), ("details", "Подробности", "text")],
                               empty_text="Нет записей")
        v.addWidget(self.audit, 1)
        self.tabs.addTab(w, "Журнал действий")

    def load_audit(self):
        if self.ctx.can(Perm.AUDIT_VIEW):
            self.audit.set_rows(list_audit(self.ctx, self.audit_q.text()))

    # ------------------------------------------------------------------ ИИ и OCR
    def _ai(self):
        w, v, form = self._form_tab("ИИ и распознавание")
        self.llm_on = QCheckBox("Использовать локальную языковую модель (режим B)")
        self.llm_kind = combo([("Ollama", "ollama"), ("OpenAI-совместимый сервер (llama.cpp, LM Studio)", "openai")])
        self.llm_url = QLineEdit()
        self.llm_model = QLineEdit()
        self.llm_model.setPlaceholderText("например, qwen2.5:7b-instruct")
        self.rules_on = QCheckBox("Использовать встроенный стартовый набор правил диагностики")
        self.ocr_path = QLineEdit()
        self.ocr_path.setPlaceholderText("Путь к tesseract.exe (если установлен не в стандартную папку)")
        self.ocr_langs = QLineEdit()
        self.ocr_info = label("", "Muted")
        self.llm_info = label("", "Muted")
        for text, x in (("", self.llm_on), ("Тип сервера", self.llm_kind), ("Адрес (только этот компьютер)", self.llm_url),
                        ("Модель", self.llm_model), ("Состояние", self.llm_info), ("", self.rules_on), ("Tesseract OCR", self.ocr_path),
                        ("Языки OCR", self.ocr_langs), ("Состояние OCR", self.ocr_info)):
            form.addRow(text, x)
        row = QHBoxLayout()
        row.addWidget(button("Сохранить", self.save_ai, primary=True))
        row.addWidget(button("Проверить модель", self.check_llm))
        row.addWidget(button("Проверить OCR", self.check_ocr))
        row.addStretch()
        v.addLayout(row)
        v.addWidget(label("Модель не скачивается программой. Установите сервер модели (например, Ollama) и модель отдельно; "
                          "программа обращается только к адресу 127.0.0.1. Без модели работает базовый режим A.", "Muted"))
        v.addStretch()

    def save_ai(self):
        run(self, update_settings, self.ctx, {"llm.enabled": self.llm_on.isChecked(), "llm.kind": self.llm_kind.currentData(),
                                              "llm.url": self.llm_url.text().strip(), "llm.model": self.llm_model.text().strip(),
                                              "assistant.use_builtin_rules": self.rules_on.isChecked(),
                                              "ocr.tesseract_path": self.ocr_path.text().strip(), "ocr.languages": self.ocr_langs.text().strip() or "rus+eng"},
            success="Сохранено")

    def check_llm(self):
        try:
            model = llm.LocalLLM(self.llm_kind.currentData(), self.llm_url.text().strip(), self.llm_model.text())
        except Exception as exc:  # noqa: BLE001
            self.llm_info.setText(str(exc))
            return
        self.llm_info.setText(model.status().text)

    def check_ocr(self):
        self.ocr_info.setText(ocr_status(self.ctx).text)

    # ------------------------------------------------------------------ о программе
    def _about(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.about = label("")
        v.addWidget(self.about)
        row = QHBoxLayout()
        row.addWidget(button("Открыть папку данных", lambda: open_path(self.ctx.paths.root)))
        row.addWidget(button("Открыть журнал программы", lambda: open_path(self.ctx.paths.logs)))
        row.addStretch()
        v.addLayout(row)
        v.addStretch()
        self.tabs.addTab(w, "О программе")

    # ------------------------------------------------------------------
    def refresh(self):
        s = read_settings(self.ctx)
        for k, wdg in self.company.items():
            wdg.setText(s.get(k) or "")
        self.logo_lbl.setText(s.get("company.logo_path") or "не выбран")
        self.default_markup.set_value(s.get("pricing.default_markup"))
        self.default_markup.setEnabled(self.ctx.can(Perm.MARKUP_DEFAULT))
        self.labor_rate.set_value(s.get("pricing.labor_rate"))
        self.tax_regime.setCurrentIndex(max(self.tax_regime.findData(s.get("tax.regime") or ""), 0))
        self.vat_mode.setCurrentIndex(max(self.vat_mode.findData(s.get("tax.vat_mode") or "not_configured"), 0))
        self.vat_rate.setText(str(s.get("tax.vat_rate") or ""))
        self.receipt_width.setCurrentIndex(max(self.receipt_width.findData(int(s.get("print.receipt_width_mm") or 80)), 0))
        self.b_interval.setValue(int(float(s.get("backup.local_interval_hours") or 2)))
        self.b_keep.setValue(int(s.get("backup.local_keep") or 24))
        self.b_ext.setText(s.get("backup.external_path") or "")
        self.b_ext_keep.setValue(int(s.get("backup.external_keep") or 30))
        self.b_daily.setChecked(bool(s.get("backup.external_daily")))
        self.b_weekly.setChecked(bool(s.get("backup.verify_weekly")))
        self.b_monthly.setChecked(bool(s.get("backup.monthly_reminder")))
        self.b_minfree.setValue(int(s.get("backup.min_free_mb") or 200))
        self.enc_lbl.setText("включено (пароль хранится защищённым средствами Windows)" if s.get("backup.encrypt") else "выключено")
        admin_backup = self.ctx.can(Perm.BACKUP_CONFIGURE)
        for b in self.backup_buttons:
            b.setEnabled(admin_backup)
        self.b_restore.setEnabled(self.ctx.can(Perm.BACKUP_RESTORE))
        self.history.set_rows([{**h, "size_mb": f"{(h['size_bytes'] or 0) / 1024 / 1024:.1f}"} for h in list_history(self.ctx)])
        if self.ctx.can(Perm.USERS_MANAGE):
            self.users.set_rows(auth.list_users(self.ctx))
        for b in self.user_buttons:
            b.setEnabled(self.ctx.can(Perm.USERS_MANAGE))
        self.emps.set_rows(list_employees(self.ctx, include_inactive=True))
        self.load_audit()
        self.llm_on.setChecked(bool(s.get("llm.enabled")))
        self.llm_kind.setCurrentIndex(max(self.llm_kind.findData(s.get("llm.kind") or "ollama"), 0))
        self.llm_url.setText(s.get("llm.url") or "")
        self.llm_model.setText(s.get("llm.model") or "")
        self.rules_on.setChecked(bool(s.get("assistant.use_builtin_rules")))
        self.ocr_path.setText(s.get("ocr.tesseract_path") or "")
        self.ocr_langs.setText(s.get("ocr.languages") or "rus+eng")
        self.about.setText(
            f"{APP_NAME} {__version__}\n\nПапка данных: {self.ctx.paths.root}\nБаза данных: {self.ctx.paths.db_file}\n"
            f"Версия схемы базы: {current_revision(self.ctx.paths.db_file)}\nВложения: {self.ctx.paths.attachments}\n"
            f"Резервные копии: {self.ctx.paths.backups}\n\nПрограмма работает полностью локально: без интернета, без облачных сервисов "
            f"и без передачи данных третьим лицам.\nФискализация (ККТ) не подключена — печатаются только нефискальные документы.")

    def handle(self, action, **kw):
        if action == "backup":
            self.tabs.setCurrentIndex(2)


__all__ = ["SettingsPage", "fmt", "confirm"]
