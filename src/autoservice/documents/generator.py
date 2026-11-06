"""Документы автосервиса. Формируются заново из данных базы — повторная печать в любой момент."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, Spacer

from autoservice.application import purchasing, sales, vehicles, work_orders
from autoservice.application.context import AppContext
from autoservice.application.reports import ReportTable
from autoservice.application.settings_service import read_settings
from autoservice.documents import pdf
from autoservice.documents.pdf import esc
from autoservice.domain.enums import WOStatus
from autoservice.domain.errors import ValidationError
from autoservice.domain.money import ZERO, D, fmt_money, fmt_qty, money
from autoservice.security.safe_paths import safe_filename

NON_FISCAL = "НЕФИСКАЛЬНЫЙ ДОКУМЕНТ. Не является кассовым чеком."


def output_path(ctx: AppContext, kind: str, number: str, ext: str = "pdf") -> Path:
    folder = ctx.paths.exports / "documents" / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return folder / safe_filename(f"{kind}_{number}_{stamp}.{ext}")


def _vat_lines(settings: dict, total: Decimal) -> list[tuple[str, object, bool]]:
    mode = settings.get("tax.vat_mode") or "not_configured"
    rate = settings.get("tax.vat_rate")
    if mode == "included" and rate not in (None, ""):
        r = D(rate)
        vat = money(total * r / (Decimal(100) + r))
        return [(f"В том числе НДС {fmt_qty(r)}%:", vat, False)]
    if mode == "not_payer":
        return [("НДС:", "не облагается", False)]
    return []


def _date(value) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _wo_head(wo: dict, st: dict, width: float, settings: dict, title: str) -> list:
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"{title} № {wo['number']}"), st["title"]))
    story.append(Paragraph(esc(f"Дата: {_date(wo['opened_at'])}" + (f" · Дата выдачи: {_date(wo['closed_at'])}" if wo["closed_at"] else "")), st["normal"]))
    story.append(Spacer(1, 3 * mm))
    pairs = [
        ("Автомобиль", wo["vehicle"]),
        ("Государственный номер", wo["plate"]),
        ("VIN", wo["vin"]),
        ("Пробег, км", f"{wo['mileage']:,}".replace(",", " ") if wo["mileage"] is not None else ""),
        ("Клиент", wo["customer"]),
        ("Телефон", wo["customer_phone"]),
        ("Ответственный механик", wo["mechanic"]),
    ]
    story.append(pdf.kv_table(pairs, st, width))
    return story


def _services_rows(services: list[dict]) -> list[list]:
    return [[i, x["name"], x["qty"], x["unit_price"], x["discount_amount"] or None, x["amount_net"]] for i, x in enumerate(services, 1)]


def _parts_rows(parts: list[dict]) -> list[list]:
    return [[i, p["part_name"], p["oem_number"], f"{fmt_qty(p['qty'])} {p['unit']}", p["unit_price"], p["discount_amount"] or None, p["amount_net"]]
            for i, p in enumerate(parts, 1)]


def _lines_tables(story, st, width, services, parts, services_title="Работы", parts_title="Запчасти и материалы"):
    if services:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(esc(services_title), st["bold"]))
        story.append(pdf.items_table(["№", "Наименование работы", "Кол-во", "Цена", "Скидка", "Сумма"], _services_rows(services),
                                     ["int", "text", "qty", "money", "money", "money"],
                                     [8 * mm, width - 8 * mm - 18 * mm - 26 * mm * 3, 18 * mm, 26 * mm, 26 * mm, 26 * mm], st,
                                     ["", "Итого работы", None, None, sum((x["discount_amount"] for x in services), ZERO) or None,
                                      sum((x["amount_net"] for x in services), ZERO)]))
    if parts:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(esc(parts_title), st["bold"]))
        story.append(pdf.items_table(["№", "Наименование", "Артикул", "Кол-во", "Цена", "Скидка", "Сумма"], _parts_rows(parts),
                                     ["int", "text", "text", "text", "money", "money", "money"],
                                     [8 * mm, width - 8 * mm - 30 * mm - 18 * mm - 24 * mm * 3, 30 * mm, 18 * mm, 24 * mm, 24 * mm, 24 * mm], st,
                                     ["", "Итого запчасти", "", "", None, sum((p["discount_amount"] for p in parts), ZERO) or None,
                                      sum((p["amount_net"] for p in parts), ZERO)]))


def _payments_block(wo: dict, total: Decimal, st, width, settings) -> list:
    pairs = [("Итого:", total, True)] + _vat_lines(settings, total)
    pairs.append(("Оплачено:", wo["paid"], False))
    rest = money(total - wo["paid"])
    pairs.append(("К оплате:" if rest >= 0 else "Переплата:", abs(rest), True))
    return [Spacer(1, 3 * mm), pdf.totals_block(pairs, st, width)]


def _footer(settings) -> str:
    return (settings.get("company.document_footer") or "").strip()


# --------------------------------------------------------------------------- заказ-наряд и связанные


def work_order_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    wo = work_orders.get_work_order(ctx, wo_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = _wo_head(wo, st, width, settings, "Заказ-наряд")
    story.append(pdf.kv_table([("Статус", wo["status_title"]), ("Жалоба клиента", wo["complaint"]),
                               ("Результаты диагностики", wo["diagnosis"]), ("Рекомендации", wo["recommendations"])], st, width))
    closed = wo["status"] in (WOStatus.READY, WOStatus.ISSUED)
    services = [x for x in wo["services"] if x["is_done"]] if closed else wo["services"]
    parts = [p for p in wo["parts"] if p["qty"] > 0]
    _lines_tables(story, st, width, services, parts)
    total = wo["total_amount"] if closed else wo["estimate_amount"]
    story += _payments_block(wo, total, st, width, settings)
    if not closed:
        story.append(Paragraph(esc("Ремонт не завершён: суммы предварительные."), st["small"]))
    if wo["notes"]:
        story.append(Paragraph(esc(f"Примечания: {wo['notes']}"), st["small"]))
    story.append(pdf.signatures(["Исполнитель", "Заказчик"], st, width))
    return pdf.build_a4(path or output_path(ctx, "zakaz-naryad", wo["number"]), story, f"Заказ-наряд {wo['number']}", _footer(settings))


def estimate_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    wo = work_orders.get_work_order(ctx, wo_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = _wo_head(wo, st, width, settings, "Предварительная смета к заказ-наряду")
    story.append(pdf.kv_table([("Жалоба клиента", wo["complaint"]), ("Результаты диагностики", wo["diagnosis"])], st, width))
    parts = [p for p in wo["parts"] if p["qty"] > 0]
    _lines_tables(story, st, width, wo["services"], parts, "Планируемые работы", "Планируемые запчасти и материалы")
    pairs = [("Предварительная стоимость:", wo["estimate_amount"], True)] + _vat_lines(settings, wo["estimate_amount"])
    story += [Spacer(1, 3 * mm), pdf.totals_block(pairs, st, width)]
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(esc("Смета предварительная. Окончательная стоимость определяется по фактически выполненным работам "
                               "и установленным запчастям и согласовывается с заказчиком."), st["small"]))
    story.append(pdf.signatures(["Исполнитель", "Смету согласовал (заказчик)"], st, width))
    return pdf.build_a4(path or output_path(ctx, "smeta", wo["number"]), story, f"Смета {wo['number']}", _footer(settings))


def _require_finished(wo: dict) -> None:
    if wo["status"] not in (WOStatus.READY, WOStatus.ISSUED):
        raise ValidationError("Документ формируется после завершения ремонта (статус «Готов» или «Выдан»)")


def act_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    wo = work_orders.get_work_order(ctx, wo_id)
    _require_finished(wo)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = _wo_head(wo, st, width, settings, "Акт выполненных работ к заказ-наряду")
    services = [x for x in wo["services"] if x["is_done"]]
    parts = [p for p in wo["parts"] if p["qty"] > 0 and p["issued_qty"] == p["qty"]]
    _lines_tables(story, st, width, services, parts, "Выполненные работы", "Установленные запчасти и материалы")
    pairs = [("Итого по акту:", wo["total_amount"], True)] + _vat_lines(settings, wo["total_amount"])
    story += [Spacer(1, 3 * mm), pdf.totals_block(pairs, st, width)]
    story.append(Paragraph(esc("Работы выполнены в полном объёме. Заказчик претензий по объёму, качеству и срокам "
                               "выполнения работ не имеет."), st["normal"]))
    story.append(pdf.signatures(["Исполнитель", "Заказчик"], st, width))
    return pdf.build_a4(path or output_path(ctx, "akt", wo["number"]), story, f"Акт {wo['number']}", _footer(settings))


def service_receipt_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    """Квитанция об обслуживании (A4): перечень выполненного и итог."""
    wo = work_orders.get_work_order(ctx, wo_id)
    _require_finished(wo)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"Квитанция об обслуживании · Заказ-наряд № {wo['number']}"), st["title"]))
    story.append(pdf.kv_table([("Автомобиль", wo["vehicle"]), ("Государственный номер", wo["plate"]),
                               ("Дата", _date(wo["closed_at"] or wo["opened_at"]))], st, width))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(esc("Выполненные работы:"), st["bold"]))
    rows = []
    n = 0
    for x in wo["services"]:
        if x["is_done"]:
            n += 1
            rows.append([n, x["name"], "" if x["qty"] == 1 else fmt_qty(x["qty"]), x["unit_price"] if x["qty"] != 1 else None, x["amount_net"]])
    for p in wo["parts"]:
        if p["qty"] > 0 and p["issued_qty"] == p["qty"]:
            n += 1
            rows.append([n, p["part_name"], f"{fmt_qty(p['qty'])} {p['unit']}", p["unit_price"], p["amount_net"]])
    story.append(pdf.items_table(["№", "Наименование", "Кол-во", "Цена", "Сумма"], rows, ["int", "text", "text", "money", "money"],
                                 [8 * mm, width - 8 * mm - 22 * mm - 30 * mm * 2, 22 * mm, 30 * mm, 30 * mm], st))
    story += _payments_block(wo, wo["total_amount"], st, width, settings)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(esc(NON_FISCAL), st["small"]))
    return pdf.build_a4(path or output_path(ctx, "kvitanciya", wo["number"]), story, f"Квитанция {wo['number']}", _footer(settings))


def installed_parts_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    wo = work_orders.get_work_order(ctx, wo_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = _wo_head(wo, st, width, settings, "Список установленных запчастей к заказ-наряду")
    parts = [p for p in wo["parts"] if p["issued_qty"] > 0]
    rows = [[i, p["part_name"], p["brand"], p["oem_number"], f"{fmt_qty(p['issued_qty'])} {p['unit']}",
             "расходный материал" if p["is_consumable"] else "запчасть"] for i, p in enumerate(parts, 1)]
    story.append(Spacer(1, 3 * mm))
    if rows:
        story.append(pdf.items_table(["№", "Наименование", "Бренд", "Артикул", "Кол-во", "Вид"], rows,
                                     ["int", "text", "text", "text", "text", "text"],
                                     [8 * mm, width - 8 * mm - 28 * mm - 34 * mm - 20 * mm - 30 * mm, 28 * mm, 34 * mm, 20 * mm, 30 * mm], st))
    else:
        story.append(Paragraph(esc("Запчасти со склада не выдавались."), st["normal"]))
    story.append(pdf.signatures(["Выдал (кладовщик/мастер)", "Получил (механик)"], st, width))
    return pdf.build_a4(path or output_path(ctx, "zapchasti", wo["number"]), story, f"Запчасти {wo['number']}", _footer(settings))


def internal_waybill_wo_pdf(ctx: AppContext, wo_id: int, path: Path | None = None) -> Path:
    """Накладная внутреннего учёта: выдача со склада в ремонт (себестоимость — для учёта)."""
    wo = work_orders.get_work_order(ctx, wo_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"Накладная внутреннего учёта на выдачу в ремонт · Заказ-наряд № {wo['number']}"), st["title"]))
    story.append(pdf.kv_table([("Автомобиль", f"{wo['vehicle']} {wo['plate']}"), ("Дата", _date(datetime.now()))], st, width))
    parts = [p for p in wo["parts"] if p["issued_qty"] > 0]
    rows = [[i, p["part_code"], p["part_name"], p["oem_number"], f"{fmt_qty(p['issued_qty'])} {p['unit']}", p["cogs"] if p["cogs_known"] else None]
            for i, p in enumerate(parts, 1)]
    story.append(Spacer(1, 3 * mm))
    total = sum((p["cogs"] for p in parts if p["cogs_known"] and p["cogs"] is not None), ZERO)
    story.append(pdf.items_table(["№", "Код", "Наименование", "Артикул", "Кол-во", "Себестоимость"], rows,
                                 ["int", "text", "text", "text", "text", "money"],
                                 [8 * mm, 20 * mm, width - 8 * mm - 20 * mm - 32 * mm - 20 * mm - 28 * mm, 32 * mm, 20 * mm, 28 * mm], st,
                                 ["", "", "Итого", "", "", total]))
    story.append(Paragraph(esc("Документ внутреннего учёта склада. Не передаётся клиенту."), st["small"]))
    story.append(pdf.signatures(["Выдал", "Получил"], st, width))
    return pdf.build_a4(path or output_path(ctx, "nakladnaya-vydacha", wo["number"]), story, "Накладная", _footer(settings))


# --------------------------------------------------------------------------- продажа и закупка


def sale_pdf(ctx: AppContext, sale_id: int, path: Path | None = None) -> Path:
    sale = sales.get_sale(ctx, sale_id)
    if sale["status"] != "posted":
        raise ValidationError("Документ продажи формируется для проведённой продажи")
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"Документ о продаже запчастей № {sale['number']}"), st["title"]))
    story.append(pdf.kv_table([("Дата", _date(sale["doc_date"])), ("Покупатель", sale["customer"])], st, width))
    rows = [[i["line_no"], i["part_name"], i["oem_number"], f"{fmt_qty(i['qty'])} {i['unit']}", i["unit_price"], i["discount_amount"] or None, i["amount_net"]]
            for i in sale["items"]]
    story.append(Spacer(1, 3 * mm))
    story.append(pdf.items_table(["№", "Наименование", "Артикул", "Кол-во", "Цена", "Скидка", "Сумма"], rows,
                                 ["int", "text", "text", "text", "money", "money", "money"],
                                 [8 * mm, width - 8 * mm - 30 * mm - 18 * mm - 24 * mm * 3, 30 * mm, 18 * mm, 24 * mm, 24 * mm, 24 * mm], st))
    pairs = [("Итого:", sale["total_net"], True)] + _vat_lines(settings, sale["total_net"])
    if sale["returned"]:
        pairs.append(("Возвращено покупателю:", sale["returned"], False))
    pairs += [("Оплачено:", sale["paid"], False), ("К оплате:", sale["debt"], True)]
    story += [Spacer(1, 3 * mm), pdf.totals_block(pairs, st, width), Spacer(1, 2 * mm), Paragraph(esc(NON_FISCAL), st["small"])]
    story.append(pdf.signatures(["Продавец", "Покупатель"], st, width))
    return pdf.build_a4(path or output_path(ctx, "prodazha", sale["number"]), story, f"Продажа {sale['number']}", _footer(settings))


def internal_waybill_purchase_pdf(ctx: AppContext, purchase_id: int, path: Path | None = None) -> Path:
    po = purchasing.get_purchase(ctx, purchase_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"Накладная внутреннего учёта на поступление № {po['number']}"), st["title"]))
    story.append(pdf.kv_table([("Дата", _date(po["doc_date"])), ("Поставщик", po["supplier"]),
                               ("Документ поставщика", f"{po['supplier_doc_number']} от {_date(po['supplier_doc_date'])}".strip(" от")),
                               ("Статус", po["status_title"])], st, width))
    rows = [[i["line_no"], i["part_code"], i["part_name"], i["oem_number"], f"{fmt_qty(i['qty'])} {i['unit']}", i["unit_price"], i["amount"],
             i["allocated_extra"] or None] for i in po["items"]]
    story.append(Spacer(1, 3 * mm))
    story.append(pdf.items_table(["№", "Код", "Наименование", "Артикул", "Кол-во", "Цена", "Сумма", "Доп. затр."], rows,
                                 ["int", "text", "text", "text", "text", "money", "money", "money"],
                                 [8 * mm, 18 * mm, width - 8 * mm - 18 * mm - 28 * mm - 16 * mm - 22 * mm * 3, 28 * mm, 16 * mm, 22 * mm, 22 * mm, 22 * mm], st))
    pairs = [("Товары:", po["total_goods"], False), ("Доп. затраты:", po["extra_costs"], False), ("Итого:", po["total"], True),
             ("Оплачено поставщику:", po["paid"], False), ("Долг поставщику:", po["outstanding"], True)]
    story += [Spacer(1, 3 * mm), pdf.totals_block(pairs, st, width)]
    story.append(pdf.signatures(["Принял", "Проверил"], st, width))
    return pdf.build_a4(path or output_path(ctx, "nakladnaya-postuplenie", po["number"]), story, "Накладная", _footer(settings))


# --------------------------------------------------------------------------- автомобиль


def vehicle_report_pdf(ctx: AppContext, vehicle_id: int, path: Path | None = None) -> Path:
    v = vehicles.get_vehicle(ctx, vehicle_id)
    settings = read_settings(ctx)
    st, width = pdf.styles(), pdf.content_width()
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(f"Отчёт по автомобилю: {v['title']}"), st["title"]))
    vol = f"{fmt_qty(v['engine_volume'])} л" if v["engine_volume"] else ""
    story.append(pdf.kv_table([("Госномер", v["plate"]), ("VIN", v["vin"] or "не указан"), ("Поколение", v["generation"]),
                               ("Двигатель", " ".join(x for x in (v["engine_type"], v["engine_code"], vol, v["fuel"]) if x)),
                               ("Трансмиссия / привод", " / ".join(x for x in (v["transmission"], v["drive"]) if x)),
                               ("Пробег, км", str(v["mileage"] or "")), ("Владелец", v["owner"]), ("Телефон", v["owner_phone"])], st, width))
    if len(v["plates"]) > 1:
        story.append(Paragraph(esc("Прежние госномера: " + ", ".join(p["plate"] for p in v["plates"][:-1])), st["small"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(esc("История обслуживания"), st["bold"]))
    if not v["history"]:
        story.append(Paragraph(esc("Обращений пока не было."), st["normal"]))
    for h in v["history"]:
        story.append(Spacer(1, 2 * mm))
        story.append(HRFlowable(width="100%", thickness=0.4))
        story.append(Paragraph(esc(f"Заказ-наряд № {h['number']} от {_date(h['opened_at'])} — {h['status_title']}, "
                                   f"пробег {h['mileage'] or '—'} км, сумма {fmt_money(h['total'])}"), st["bold"]))
        if h["complaint"]:
            story.append(Paragraph(esc(f"Жалоба: {h['complaint']}"), st["normal"]))
        if h["diagnosis"]:
            story.append(Paragraph(esc(f"Диагностика: {h['diagnosis']}"), st["normal"]))
        if h["services"]:
            story.append(Paragraph(esc("Работы: " + "; ".join(h["services"])), st["normal"]))
        if h["parts"]:
            story.append(Paragraph(esc("Запчасти: " + "; ".join(h["parts"])), st["normal"]))
    return pdf.build_a4(path or output_path(ctx, "avtomobil", v["plate"] or str(v["id"])), story, "Отчёт по автомобилю", _footer(settings))


# --------------------------------------------------------------------------- отчёты


def report_pdf(ctx: AppContext, table: ReportTable, path: Path | None = None) -> Path:
    settings = read_settings(ctx)
    landscape = len(table.columns) > 6
    st, width = pdf.styles(8.5 if landscape else 9.5), pdf.content_width(landscape)
    story = pdf.company_header(settings, st)
    story.append(Paragraph(esc(table.title), st["title"]))
    if table.period:
        story.append(Paragraph(esc(f"Период: {_date(table.period[0])} — {_date(table.period[1])}"
                                   + (f" · Фильтры: {table.filters_text}" if table.filters_text else "")), st["normal"]))
    story.append(Spacer(1, 3 * mm))
    total_w = sum(c.width for c in table.columns)
    widths = [width * c.width / total_w for c in table.columns]
    rows = [[r.get(c.key) for c in table.columns] for r in table.rows]
    totals = None
    if table.totals and any(v is not None for v in table.totals.values()):
        totals = [table.totals.get(c.key) if c.key in table.totals else ("Итого" if i == 0 else None) for i, c in enumerate(table.columns)]
    if rows:
        story.append(pdf.items_table([c.title for c in table.columns], rows, [c.kind if c.kind != "int" else "int" for c in table.columns],
                                     widths, st, totals))
    else:
        story.append(Paragraph(esc("Нет данных за выбранный период."), st["normal"]))
    for note in table.notes:
        story.append(Paragraph(esc(note), st["small"]))
    return pdf.build_a4(path or output_path(ctx, table.code, datetime.now().strftime("%Y%m%d")), story, table.title, _footer(settings), landscape)


# --------------------------------------------------------------------------- чековая лента


def thermal_receipt_pdf(ctx: AppContext, kind: str, doc_id: int, path: Path | None = None, width_mm: int | None = None) -> Path:
    """Компактная нефискальная квитанция для чекового принтера (58/80 мм)."""
    settings = read_settings(ctx)
    width_mm = int(width_mm or settings.get("print.receipt_width_mm") or 80)
    if width_mm not in (58, 80):
        raise ValidationError("Ширина ленты: 58 или 80 мм")
    base = 7.5 if width_mm == 58 else 8.5
    st = pdf.styles(base)
    name = (settings.get("company.name") or "").strip() or "АВТОСЕРВИС"
    blocks = [Paragraph(esc(name), st["center"])]
    for key in ("company.address", "company.phone"):
        if (settings.get(key) or "").strip():
            blocks.append(Paragraph(esc(settings[key]), st["small"]))
    blocks.append(HRFlowable(width="100%", thickness=0.5))
    lines: list[tuple[str, Decimal]] = []
    if kind == "work_order":
        wo = work_orders.get_work_order(ctx, doc_id)
        _require_finished(wo)
        blocks.append(Paragraph(esc(f"Заказ-наряд № {wo['number']}"), st["bold"]))
        blocks.append(Paragraph(esc(f"Автомобиль: {wo['vehicle']}"), st["normal"]))
        blocks.append(Paragraph(esc(f"Госномер: {wo['plate']}"), st["normal"]))
        blocks.append(Paragraph(esc(f"Дата: {_date(wo['closed_at'] or wo['opened_at'])}"), st["normal"]))
        for x in wo["services"]:
            if x["is_done"]:
                lines.append((x["name"] if x["qty"] == 1 else f"{x['name']} — {fmt_qty(x['qty'])} × {fmt_money(x['unit_price'])}", x["amount_net"]))
        for p in wo["parts"]:
            if p["qty"] > 0 and p["issued_qty"] == p["qty"]:
                lines.append((f"{p['part_name']} — {fmt_qty(p['qty'])} {p['unit']} × {fmt_money(p['unit_price'])}", p["amount_net"]))
        total, paid, number = wo["total_amount"], wo["paid"], wo["number"]
    elif kind == "sale":
        sale = sales.get_sale(ctx, doc_id)
        if sale["status"] != "posted":
            raise ValidationError("Квитанция формируется для проведённой продажи")
        blocks.append(Paragraph(esc(f"Продажа № {sale['number']}"), st["bold"]))
        blocks.append(Paragraph(esc(f"Дата: {_date(sale['doc_date'])}"), st["normal"]))
        for i in sale["items"]:
            lines.append((f"{i['part_name']} — {fmt_qty(i['qty'])} {i['unit']} × {fmt_money(i['unit_price'])}", i["amount_net"]))
        total, paid, number = sale["total_net"], sale["paid"], sale["number"]
    else:
        raise ValidationError("Неизвестный тип квитанции")
    blocks.append(HRFlowable(width="100%", thickness=0.5))
    for i, (text, amount) in enumerate(lines, 1):
        blocks.append(Paragraph(esc(f"{i}. {text}"), st["normal"]))
        blocks.append(Paragraph(esc(fmt_money(amount)), st["right"]))
    blocks.append(HRFlowable(width="100%", thickness=0.5))
    blocks.append(Paragraph(esc(f"Итого: {fmt_money(total)}"), st["right_bold"]))
    for label, value, _ in _vat_lines(settings, total):
        blocks.append(Paragraph(esc(f"{label} {fmt_money(value) if isinstance(value, Decimal) else value}"), st["right"]))
    blocks.append(Paragraph(esc(f"Оплачено: {fmt_money(paid)}"), st["right"]))
    if total - paid > 0:
        blocks.append(Paragraph(esc(f"К оплате: {fmt_money(total - paid)}"), st["right_bold"]))
    blocks.append(Spacer(1, 2 * mm))
    blocks.append(Paragraph(esc(NON_FISCAL), st["center"]))
    return pdf.build_receipt(path or output_path(ctx, f"chek-{width_mm}mm", number), blocks, width_mm)


DOCUMENTS_FOR_WORK_ORDER = {
    "work_order": ("Заказ-наряд", work_order_pdf),
    "estimate": ("Предварительная смета", estimate_pdf),
    "act": ("Акт выполненных работ", act_pdf),
    "receipt": ("Квитанция об обслуживании (A4)", service_receipt_pdf),
    "receipt_thermal": ("Квитанция для чекового принтера", lambda ctx, i, path=None: thermal_receipt_pdf(ctx, "work_order", i, path)),
    "installed_parts": ("Список установленных запчастей", installed_parts_pdf),
    "waybill": ("Накладная внутреннего учёта (выдача)", internal_waybill_wo_pdf),
}
