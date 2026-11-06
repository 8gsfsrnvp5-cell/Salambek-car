"""Самопроверка установленной программы: AutoServiceAI.exe --self-test [отчёт.json].

Работает во ВРЕМЕННОЙ папке и не трогает рабочую базу. Проверяет: создание
базы и миграции, операции склада/продаж/ремонта, PDF, печать через QPrinter,
Excel, резервную копию с проверкой и восстановлением, помощника, отсутствие
сетевых соединений. Рабочая папка данных только проверяется на доступ к записи.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from autoservice import APP_NAME, __version__


def run_self_test(report_path: str | None = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    results: list[dict] = []
    network_attempts: list[str] = []
    real_connect = socket.socket.connect

    def guard(self, address):
        network_attempts.append(str(address))
        raise OSError("Сетевые соединения запрещены во время самопроверки")

    socket.socket.connect = guard

    def step(name, fn):
        try:
            detail = fn()
            results.append({"step": name, "ok": True, "detail": detail or ""})
        except Exception as exc:  # noqa: BLE001
            results.append({"step": name, "ok": False, "detail": f"{exc}\n{traceback.format_exc()[-1500:]}"})

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    tmp = Path(tempfile.mkdtemp(prefix="autoservice-selftest-"))
    state: dict = {}
    try:
        from decimal import Decimal

        from autoservice.application import auth, bootstrap, catalog, purchasing, sales, vehicles, work_orders
        from autoservice.config.paths import AppPaths, resolve_data_root
        from autoservice.domain.enums import WOStatus

        def data_dir_check():
            root = resolve_data_root()
            root.mkdir(parents=True, exist_ok=True)
            probe = root / f".selftest-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return f"рабочая папка данных доступна для записи: {root}"

        step("Папка данных", data_dir_check)

        def start():
            res = bootstrap.start(AppPaths(tmp / "data"), with_logging=False)
            admin = auth.create_first_admin(res.ctx, "selftest", "Selftest-123")
            state["ctx"] = res.ctx.as_user(admin)
            return f"база создана, схема {res.migration.to_revision}"

        step("База данных и миграции", start)
        ctx = state.get("ctx")

        def operations():
            sup = catalog.save_supplier(ctx, {"name": "Проверочный поставщик"})
            part = catalog.create_part(ctx, {"name": "Проверочная деталь", "oem_number": "TEST-1"})
            po = purchasing.create_purchase(ctx, sup, "T-1")
            purchasing.add_purchase_line(ctx, po, part, "10", "100")
            purchasing.post_purchase(ctx, po)
            sid = sales.create_sale(ctx)
            sales.add_sale_line(ctx, sid, part, "2", markup_override="30")
            sales.post_sale(ctx, sid, "cash")
            assert sales.get_sale(ctx, sid)["total_net"] == Decimal("260.00")
            vid, _ = vehicles.create_vehicle(ctx, {"make": "Проверка", "plate": "А000АА00"})
            wo = work_orders.create_work_order(ctx, vid, "Проверка")
            work_orders.add_service(ctx, wo, "Диагностика", "500", done=True)
            line = work_orders.add_part(ctx, wo, part, "1", markup_override="0")["id"]
            work_orders.issue_part(ctx, line)
            work_orders.change_status(ctx, wo, WOStatus.READY)
            work_orders.change_status(ctx, wo, WOStatus.ISSUED)
            state.update(wo=wo, sale=sid)
            return "закупка, продажа (FIFO), заказ-наряд выполнены"

        step("Складские и ремонтные операции", operations)

        def documents():
            from autoservice.documents import generator, printing

            pdf = generator.work_order_pdf(ctx, state["wo"])
            printed = printing.print_to_pdf_file(pdf, tmp / "print.pdf")
            generator.thermal_receipt_pdf(ctx, "sale", state["sale"])
            return f"PDF сформирован, через принтер выведено страниц: {printed}"

        step("Документы PDF и печать", documents)

        def excel_export():
            from autoservice.application.reports import run_report
            from autoservice.documents.excel import export_xlsx

            export_xlsx(run_report(ctx, "summary"), tmp / "summary.xlsx")
            return "сводный отчёт выгружен в Excel"

        step("Отчёты и Excel", excel_export)

        def backup():
            from autoservice.backup.service import create_backup, restore_backup, verify_backup

            info = create_backup(ctx, tmp / "backup", create_dir=True)
            res = verify_backup(info["path"], work_dir=tmp)
            assert res.ok, res.messages
            restore_backup(ctx, info["path"])
            return "копия создана, проверена и восстановлена"

        step("Резервное копирование", backup)

        def assistant():
            from autoservice.ai.assistant import DiagnosisInput, analyze
            from autoservice.application.bootstrap import start as restart

            c2 = restart(AppPaths(tmp / "data"), with_logging=False).ctx
            user = auth.login(c2, "selftest", "Selftest-123")
            r = analyze(c2.as_user(user), DiagnosisInput(make="Toyota", complaint="горит P0300"))
            return f"базовый помощник: {r['overall_title']}"

        step("ИИ-помощник (режим A)", assistant)

        def ocr():
            from autoservice.ai.ocr import tesseract_status

            return tesseract_status().text

        step("OCR (информационно)", ocr)
        step("Отсутствие сетевых соединений", lambda: (_ for _ in ()).throw(AssertionError(network_attempts)) if network_attempts else "сетевых попыток нет")
    finally:
        socket.socket.connect = real_connect
    required_ok = all(r["ok"] for r in results if r["step"] != "OCR (информационно)")
    report = {"app": APP_NAME, "version": __version__, "time": datetime.now().isoformat(timespec="seconds"),
              "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)), "ok": required_ok, "steps": results}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    else:
        try:
            print(text)
        except Exception:  # noqa: BLE001 — оконная сборка без консоли
            pass
    return 0 if required_ok else 1
