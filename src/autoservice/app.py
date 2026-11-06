"""Точка входа AutoService AI."""

from __future__ import annotations

import argparse
import logging
import sys

from autoservice import APP_ID, APP_NAME, __version__

log = logging.getLogger(__name__)


def _parse(argv):
    p = argparse.ArgumentParser(prog="AutoServiceAI", description=APP_NAME)
    p.add_argument("--self-test", metavar="REPORT", nargs="?", const="", help="проверка работоспособности установленной программы")
    p.add_argument("--version", action="store_true")
    return p.parse_args(argv)


def run_gui() -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from autoservice.application import auth, bootstrap
    from autoservice.config.paths import AppPaths, resource_path
    from autoservice.domain.errors import DomainError
    from autoservice.ui.main_window import FirstRunDialog, LoginDialog, MainWindow
    from autoservice.ui.theme import STYLE

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_ID)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10))
    icon = resource_path("assets", "autoservice.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    try:
        paths = AppPaths.default().ensure()
    except OSError as exc:
        QMessageBox.critical(None, APP_NAME, f"Нет доступа к папке данных программы:\n{exc}\n\nОбратитесь к администратору компьютера.")
        return 2
    lock = QLockFile(str(paths.root / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(None, APP_NAME, "Программа уже запущена на этом компьютере.")
        return 1
    try:
        result = bootstrap.start(paths)
    except DomainError as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка запуска")
        QMessageBox.critical(None, APP_NAME, f"Не удалось открыть базу данных: {exc}\n\nПодробности: {paths.logs / 'autoservice.log'}")
        return 2
    ctx = result.ctx
    if result.migration.upgraded:
        QMessageBox.information(None, APP_NAME, f"База данных обновлена до новой версии программы.\n"
                                                f"Копия базы до обновления: {result.migration.backup_file}")
    while True:
        if not auth.has_users(ctx):
            dlg = FirstRunDialog(ctx)
        else:
            dlg = LoginDialog(ctx)
        if not dlg.exec() or dlg.user is None:
            return 0
        win = MainWindow(ctx.as_user(dlg.user))
        win.show()
        app.exec()
        if not win.relogin:
            return 0
        ctx.db.dispose()
        try:
            ctx = bootstrap.start(paths).ctx  # заново открываем базу (например, после восстановления)
        except DomainError as exc:
            QMessageBox.critical(None, APP_NAME, str(exc))
            return 2


def main(argv=None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.self_test is not None:
        from autoservice.selftest import run_self_test

        return run_self_test(args.self_test or None)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
