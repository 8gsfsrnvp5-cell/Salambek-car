"""Печать PDF через Qt: предварительный просмотр, выбор принтера, повторная печать.

PDF-страницы растеризуются QtPdf с разрешением принтера и выводятся на
QPrinter — работает с любым установленным принтером Windows, в том числе
с виртуальными («Microsoft Print to PDF»).
"""

from __future__ import annotations

from pathlib import Path

from autoservice.domain.errors import DomainError


def _load(path: Path):
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    err = doc.load(str(path))
    if err != QPdfDocument.Error.None_ or doc.pageCount() == 0:
        raise DomainError(f"Не удалось открыть PDF для печати: {path.name}")
    return doc


def render_to_printer(pdf_path: Path, printer) -> int:
    """Выводит все страницы PDF на QPrinter. Возвращает число напечатанных страниц."""
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtGui import QPainter

    doc = _load(Path(pdf_path))
    painter = QPainter()
    if not painter.begin(printer):
        raise DomainError("Принтер недоступен")
    try:
        dpi = printer.resolution()
        for i in range(doc.pageCount()):
            if i > 0:
                printer.newPage()
            size_pt = doc.pagePointSize(i)
            target = painter.viewport()
            w_px = int(size_pt.width() / 72 * dpi)
            h_px = int(size_pt.height() / 72 * dpi)
            scale = min(target.width() / max(w_px, 1), target.height() / max(h_px, 1), 1.0)
            img = doc.render(i, QSize(max(int(w_px * scale), 1), max(int(h_px * scale), 1)))
            painter.drawImage(QRect(0, 0, img.width(), img.height()), img)
        return doc.pageCount()
    finally:
        painter.end()
        doc.close()


def print_with_preview(pdf_path: Path, parent=None) -> None:
    from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setDocName(Path(pdf_path).stem)
    dialog = QPrintPreviewDialog(printer, parent)
    dialog.setWindowTitle(f"Предварительный просмотр — {Path(pdf_path).name}")
    dialog.paintRequested.connect(lambda p: render_to_printer(pdf_path, p))
    dialog.resize(1000, 800)
    dialog.exec()


def print_to_pdf_file(pdf_path: Path, output: Path) -> int:
    """Печать через тот же путь вывода на виртуальный PDF-принтер (используется в тестах и самопроверке)."""
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output))
    return render_to_printer(pdf_path, printer)
