"""Формирование PDF (A4 и чековая лента) на ReportLab с кириллическим шрифтом."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from autoservice import APP_NAME, __version__
from autoservice.config.paths import resource_path
from autoservice.domain.errors import DomainError
from autoservice.domain.money import fmt_money, fmt_percent, fmt_qty

FONT = "AS-Regular"
FONT_BOLD = "AS-Bold"

_FONT_CANDIDATES = [
    (resource_path("assets", "fonts", "DejaVuSans.ttf"), resource_path("assets", "fonts", "DejaVuSans-Bold.ttf")),
    (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf", Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf"),
    (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf", Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuib.ttf"),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
]
_registered = False


def register_fonts() -> None:
    global _registered
    if _registered:
        return
    for regular, bold in _FONT_CANDIDATES:
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont(FONT, str(regular)))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
            pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
            _registered = True
            return
    raise DomainError("Не найден шрифт с поддержкой кириллицы (Arial или DejaVu Sans) — PDF не может быть сформирован")


def styles(base: float = 9.5) -> dict[str, ParagraphStyle]:
    register_fonts()
    return {
        "normal": ParagraphStyle("normal", fontName=FONT, fontSize=base, leading=base * 1.3),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=base - 1.5, leading=(base - 1.5) * 1.3, textColor=colors.HexColor("#444444")),
        "bold": ParagraphStyle("bold", fontName=FONT_BOLD, fontSize=base, leading=base * 1.3),
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=base + 5, leading=(base + 5) * 1.25, spaceAfter=2),
        "company": ParagraphStyle("company", fontName=FONT_BOLD, fontSize=base + 2, leading=(base + 2) * 1.25),
        "right": ParagraphStyle("right", fontName=FONT, fontSize=base, leading=base * 1.3, alignment=TA_RIGHT),
        "right_bold": ParagraphStyle("right_bold", fontName=FONT_BOLD, fontSize=base + 1, leading=(base + 1) * 1.3, alignment=TA_RIGHT),
        "center": ParagraphStyle("center", fontName=FONT_BOLD, fontSize=base, leading=base * 1.3, alignment=TA_CENTER),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=base - 0.5, leading=(base - 0.5) * 1.25),
        "cell_bold": ParagraphStyle("cell_bold", fontName=FONT_BOLD, fontSize=base - 0.5, leading=(base - 0.5) * 1.25),
    }


def esc(text) -> str:
    return (str(text) if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def fmt_value(value, kind: str) -> str:
    if value is None or value == "":
        return "" if kind == "text" else "—"
    if kind == "money":
        return fmt_money(value, currency=False)
    if kind == "qty":
        return fmt_qty(value)
    if kind == "percent":
        return fmt_percent(value)
    if kind == "date":
        return value.strftime("%d.%m.%Y") if isinstance(value, (date, datetime)) else str(value)
    if kind == "datetime":
        return value.strftime("%d.%m.%Y %H:%M") if isinstance(value, datetime) else str(value)
    return str(value)


def company_header(company: dict, st: dict) -> list:
    """Шапка с реквизитами из настроек. Незаполненные реквизиты не печатаются."""
    name = (company.get("company.name") or "").strip() or "АВТОСЕРВИС"
    lines = [Paragraph(esc(name), st["company"])]
    legal = (company.get("company.legal_name") or "").strip()
    if legal and legal != name:
        lines.append(Paragraph(esc(legal), st["small"]))
    req = []
    for key, label in (("company.inn", "ИНН"), ("company.kpp", "КПП"), ("company.ogrn", "ОГРН/ОГРНИП")):
        if (company.get(key) or "").strip():
            req.append(f"{label} {company[key].strip()}")
    if req:
        lines.append(Paragraph(esc(", ".join(req)), st["small"]))
    for key in ("company.address", "company.phone", "company.email"):
        if (company.get(key) or "").strip():
            lines.append(Paragraph(esc(company[key].strip()), st["small"]))
    logo_path = (company.get("company.logo_path") or "").strip()
    if logo_path and Path(logo_path).is_file():
        try:
            img = Image(logo_path)
            ratio = img.imageWidth / float(img.imageHeight or 1)
            h = 18 * mm
            img.drawHeight, img.drawWidth = h, min(h * ratio, 50 * mm)
            t = Table([[img, lines]], colWidths=[55 * mm, None])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            return [t, Spacer(1, 4 * mm)]
        except Exception:  # noqa: BLE001 — повреждённый логотип не должен ломать документ
            pass
    return lines + [Spacer(1, 4 * mm)]


def kv_table(pairs: list[tuple[str, str]], st: dict, width: float) -> Table:
    data = [[Paragraph(esc(k), st["small"]), Paragraph(esc(v), st["normal"])] for k, v in pairs if v not in (None, "")]
    if not data:
        return Spacer(1, 1)
    t = Table(data, colWidths=[40 * mm, width - 40 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def items_table(header: list[str], rows: list[list], kinds: list[str], widths: list[float], st: dict, total_row: list | None = None) -> Table:
    data = [[Paragraph(esc(h), st["cell_bold"]) for h in header]]
    for row in rows:
        cells = []
        for value, kind in zip(row, kinds):
            text = fmt_value(value, kind) if kind != "raw" else str(value)
            style = st["cell"]
            if kind in ("money", "qty", "percent", "int"):
                style = ParagraphStyle("num", parent=st["cell"], alignment=TA_RIGHT)
            cells.append(Paragraph(esc(text), style))
        data.append(cells)
    if total_row:
        cells = []
        for value, kind in zip(total_row, kinds):
            text = fmt_value(value, kind) if value not in (None, "") else ""
            style = ParagraphStyle("tot", parent=st["cell_bold"], alignment=TA_RIGHT if kind in ("money", "qty", "percent") else 0)
            cells.append(Paragraph(esc(text), style))
        data.append(cells)
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9a9a9a")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if total_row:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f6f6f6")))
    t.setStyle(TableStyle(style))
    return t


def totals_block(pairs: list[tuple[str, object, bool]], st: dict, width: float) -> Table:
    data = []
    for label, value, bold in pairs:
        s_label = st["right_bold"] if bold else st["right"]
        s_value = st["right_bold"] if bold else st["right"]
        data.append([Paragraph(esc(label), s_label), Paragraph(esc(fmt_money(value) if isinstance(value, Decimal) else str(value)), s_value)])
    t = Table(data, colWidths=[width - 45 * mm, 45 * mm])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def signatures(labels: list[str], st: dict, width: float) -> Table:
    cells = [[Paragraph(f"{esc(lbl)}: ____________________", st["normal"]) for lbl in labels]]
    t = Table(cells, colWidths=[width / len(labels)] * len(labels))
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 14), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([Spacer(1, 6 * mm), t])


def build_a4(path: Path, story: list, title: str, footer_text: str = "", landscape_mode: bool = False) -> Path:
    register_fonts()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pagesize = landscape(A4) if landscape_mode else A4
    generated = datetime.now().strftime("%d.%m.%Y %H:%M")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        left = f"Сформировано: {generated} · {APP_NAME} {__version__}"
        if footer_text:
            left = f"{footer_text} · {left}"
        canvas.drawString(15 * mm, 9 * mm, left[:180])
        canvas.drawRightString(pagesize[0] - 15 * mm, 9 * mm, f"Стр. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=title, author=APP_NAME, creator=APP_NAME)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path


def content_width(landscape_mode: bool = False) -> float:
    return (landscape(A4)[0] if landscape_mode else A4[0]) - 30 * mm


def build_receipt(path: Path, blocks: list, width_mm: int = 80) -> Path:
    """Чековая лента 58/80 мм. Высота страницы подбирается по содержимому."""
    register_fonts()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    width = width_mm * mm
    margin = 3 * mm
    avail = width - 2 * margin
    total_h = 0
    for b in blocks:
        _, h = b.wrap(avail, 10_000)
        total_h += h + b.getSpaceBefore() + b.getSpaceAfter()
    height = total_h + 2 * margin + 10 * mm
    doc = SimpleDocTemplate(str(path), pagesize=(width, height), leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=margin, title="Квитанция", author=APP_NAME)
    doc.build(blocks)
    return path
