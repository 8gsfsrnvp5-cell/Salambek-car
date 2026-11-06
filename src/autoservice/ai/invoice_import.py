"""Импорт накладных: файл → оригинал в хранилище → распознавание → сопоставление
с каталогом → проверка менеджером → черновик закупки. Проведение — отдельно."""

from __future__ import annotations

import difflib
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from autoservice.ai import invoice_parser, ocr
from autoservice.application import catalog, purchasing
from autoservice.application.context import AppContext, audit, get_or_404
from autoservice.application.documents_store import find_by_sha, sha256_of, store_file
from autoservice.application.settings_service import get_setting
from autoservice.domain.errors import DuplicateDocument, ValidationError
from autoservice.domain.money import money, non_negative_money, positive_qty
from autoservice.domain.normalize import normalize_text
from autoservice.knowledge import extract
from autoservice.persistence.models import InvoiceImport, Part, Supplier, now
from autoservice.security.permissions import Perm

INVOICE_EXT = {".pdf", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
STATUS_TITLES = {"needs_review": "Требует проверки", "confirmed": "Подтверждена (создан черновик закупки)",
                 "rejected": "Отклонена", "failed": "Не распознана", "duplicate": "Повторный документ"}


def ocr_status(ctx: AppContext) -> ocr.OcrStatus:
    with ctx.db.read() as s:
        path = get_setting(s, "ocr.tesseract_path") or ""
        langs = get_setting(s, "ocr.languages") or "rus+eng"
    return ocr.tesseract_status(path, langs)


def _match_supplier(s, inv: invoice_parser.ParsedInvoice) -> Supplier | None:
    if inv.supplier_inn:
        sup = s.execute(select(Supplier).where(Supplier.inn == inv.supplier_inn)).scalars().first()
        if sup:
            return sup
    if inv.supplier_name:
        target = normalize_text(inv.supplier_name).replace("«", "").replace("»", "").replace('"', "")
        for sup in s.execute(select(Supplier)).scalars():
            name = normalize_text(sup.name).replace("«", "").replace("»", "").replace('"', "")
            if name and (name == target or (len(name) > 5 and name in target)):
                return sup
    return None


def _match_lines(s, lines: list[dict], supplier_id: int | None) -> None:
    parts = None
    for ln in lines:
        ln.setdefault("flags", [])
        ln["part_id"] = None
        ln["candidates"] = []
        ln["skip"] = False
        found = catalog.find_parts_by_article(s, ln.get("article") or "", supplier_id) if ln.get("article") else []
        if len(found) == 1:
            p = found[0]
            ln["part_id"] = p.id
            ln["match_status"] = "found"
            ln["match_title"] = f"Найдено по артикулу: {p.name}"
        elif len(found) > 1:
            ln["match_status"] = "ambiguous"
            ln["candidates"] = [{"id": p.id, "name": p.name, "article": p.oem_number, "brand": p.brand, "score": 1.0} for p in found]
            ln["flags"].append("Несколько запчастей с таким артикулом — выберите вручную")
        else:
            ln["match_status"] = "not_found"
            if parts is None:
                parts = list(s.execute(select(Part).where(Part.is_active.is_(True))).scalars())
            target = normalize_text(ln.get("name"))
            scored = []
            for p in parts:
                ratio = difflib.SequenceMatcher(None, target, p.name_search).ratio()
                if ratio >= 0.6:
                    scored.append((ratio, p))
            scored.sort(key=lambda x: x[0], reverse=True)
            ln["candidates"] = [{"id": p.id, "name": p.name, "article": p.oem_number, "brand": p.brand, "score": round(r, 2)}
                                for r, p in scored[:5]]
            if ln["candidates"]:
                ln["flags"].append("Сомнительное совпадение только по названию — не принимается автоматически, выберите вручную")
            else:
                ln["flags"].append("Запчасть не найдена в каталоге — выберите или создайте карточку")


def import_invoice(ctx: AppContext, path: Path) -> int:
    ctx.require(Perm.IMPORT_INVOICES)
    path = Path(path)
    digest = sha256_of(path) if path.is_file() else ""
    with ctx.db.read() as s:
        earlier = [imp for d in find_by_sha(s, digest, "invoice")
                   for imp in s.execute(select(InvoiceImport).where(InvoiceImport.document_id == d.id)).scalars()
                   if imp.status != "rejected"]
    status = ocr_status(ctx)
    warnings: list[str] = []
    engine = ""
    parsed: invoice_parser.ParsedInvoice | None = None
    raw_text = ""
    if not earlier:
        try:
            ext = path.suffix.lower()
            if ext in (".xlsx", ".csv"):
                parsed = invoice_parser.parse_table(extract.table_rows(path))
                engine = ext[1:]
            else:
                res = extract.extract_text(path, status)
                raw_text = res.text
                engine = res.engine
                warnings += res.warnings
                if res.text.strip():
                    parsed = invoice_parser.parse_text(res.text, ocr_used=res.engine.startswith("ocr"))
        except Exception as exc:  # noqa: BLE001 — повреждённый файл не должен ронять программу
            warnings.append(f"Файл не удалось прочитать: {exc}")
    with ctx.db.transaction() as s:
        doc = store_file(s, ctx, path, "invoice", allowed=INVOICE_EXT)
        imp = InvoiceImport(document_id=doc.id, engine=engine, created_by=ctx.user_id, raw_text=raw_text[:200_000])
        if earlier:
            imp.status = "duplicate"
            imp.warnings = [f"Этот файл уже импортировался (импорт № {earlier[0].id}, статус: "
                            f"{STATUS_TITLES.get(earlier[0].status, earlier[0].status)}). Повторное поступление не создаётся."]
            imp.lines = []
        elif parsed is None:
            imp.status = "failed"
            imp.warnings = warnings or ["Текст документа не получен"]
            imp.lines = []
        else:
            sup = _match_supplier(s, parsed)
            imp.supplier_id = sup.id if sup else None
            imp.supplier_name = parsed.supplier_name or (sup.name if sup else "")
            imp.supplier_inn = parsed.supplier_inn
            imp.doc_number = parsed.doc_number
            imp.doc_date = parsed.doc_date
            imp.total_declared = money(parsed.total_declared) if parsed.total_declared is not None else None
            imp.total_calculated = parsed.total_calculated
            lines = parsed.to_json_lines()
            _match_lines(s, lines, imp.supplier_id)
            w = warnings + parsed.warnings
            if not sup:
                w.append("Поставщик не найден в справочнике — выберите или создайте его")
            dup = purchasing.find_duplicate(s, imp.supplier_id, imp.doc_number)
            if dup:
                w.append(f"Документ поставщика № {imp.doc_number} уже внесён (закупка {dup.number}) — возможен дубль")
            imp.warnings = w
            imp.lines = lines
            imp.status = "needs_review"
        s.add(imp)
        s.flush()
        audit(s, ctx, "invoice.import", "invoice_import", imp.id, status=imp.status, document_id=doc.id)
        return imp.id


def import_dict(s, imp: InvoiceImport) -> dict:
    sup = s.get(Supplier, imp.supplier_id) if imp.supplier_id else None
    return {
        "id": imp.id, "status": imp.status, "status_title": STATUS_TITLES.get(imp.status, imp.status), "engine": imp.engine,
        "document_id": imp.document_id, "supplier_id": imp.supplier_id, "supplier": sup.name if sup else "",
        "supplier_name": imp.supplier_name, "supplier_inn": imp.supplier_inn, "doc_number": imp.doc_number, "doc_date": imp.doc_date,
        "total_declared": imp.total_declared, "total_calculated": imp.total_calculated, "warnings": imp.warnings or [],
        "lines": imp.lines or [], "purchase_id": imp.purchase_id, "created_at": imp.created_at, "raw_text": imp.raw_text,
    }


def get_import(ctx: AppContext, import_id: int) -> dict:
    with ctx.db.read() as s:
        return import_dict(s, get_or_404(s, InvoiceImport, import_id, "Импорт накладной"))


def list_imports(ctx: AppContext, status: str | None = None) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(InvoiceImport).order_by(InvoiceImport.id.desc())
        if status:
            stmt = stmt.where(InvoiceImport.status == status)
        out = []
        for imp in s.execute(stmt).scalars():
            d = import_dict(s, imp)
            d.pop("raw_text")
            d["lines_count"] = len(d["lines"])
            out.append(d)
        return out


def update_line(ctx: AppContext, import_id: int, index: int, part_id: int | None = None, qty=None, price=None, skip: bool | None = None) -> None:
    """Правка строки менеджером при проверке."""
    ctx.require(Perm.IMPORT_INVOICES)
    with ctx.db.transaction() as s:
        imp = get_or_404(s, InvoiceImport, import_id, "Импорт накладной")
        if imp.status != "needs_review":
            raise ValidationError("Импорт уже обработан")
        lines = list(imp.lines or [])
        ln = dict(lines[index])
        if part_id is not None:
            get_or_404(s, Part, part_id, "Запчасть")
            ln["part_id"] = part_id
            ln["match_status"] = "manual"
            ln["match_title"] = "Выбрано менеджером"
        if qty is not None:
            ln["qty"] = str(positive_qty(qty))
        if price is not None:
            ln["price"] = str(non_negative_money(price, "Цена"))
        if skip is not None:
            ln["skip"] = bool(skip)
        lines[index] = ln
        imp.lines = lines


def set_supplier(ctx: AppContext, import_id: int, supplier_id: int) -> None:
    ctx.require(Perm.IMPORT_INVOICES)
    with ctx.db.transaction() as s:
        imp = get_or_404(s, InvoiceImport, import_id, "Импорт накладной")
        get_or_404(s, Supplier, supplier_id, "Поставщик")
        imp.supplier_id = supplier_id


def confirm_import(ctx: AppContext, import_id: int, doc_number: str | None = None, doc_date=None, allow_duplicate: bool = False) -> int:
    """Создаёт ЧЕРНОВИК закупки из проверенных строк. Склад не меняется до проведения."""
    ctx.require(Perm.IMPORT_INVOICES)
    with ctx.db.read() as s:
        imp = get_or_404(s, InvoiceImport, import_id, "Импорт накладной")
        if imp.status != "needs_review":
            raise ValidationError("Импорт уже обработан или не может быть подтверждён")
        if not imp.supplier_id:
            raise ValidationError("Выберите поставщика")
        lines = [ln for ln in (imp.lines or []) if not ln.get("skip")]
        if not lines:
            raise ValidationError("Нет строк для закупки")
        unresolved = [str(ln.get("line_no")) for ln in lines if not ln.get("part_id")]
        if unresolved:
            raise ValidationError("Не сопоставлены с каталогом строки: " + ", ".join(unresolved))
        bad = [str(ln.get("line_no")) for ln in lines if not ln.get("qty") or ln.get("price") in (None, "")]
        if bad:
            raise ValidationError("Не указано количество или цена в строках: " + ", ".join(bad))
        supplier_id, document_id = imp.supplier_id, imp.document_id
        number = doc_number if doc_number is not None else imp.doc_number
        day = doc_date or imp.doc_date
    try:
        pid = purchasing.create_purchase(ctx, supplier_id, number, supplier_doc_date=day, source="ocr_import",
                                         invoice_document_id=document_id, allow_duplicate=allow_duplicate,
                                         notes=f"Создано из распознанной накладной (импорт № {import_id}). Проверьте перед проведением.")
    except DuplicateDocument:
        raise
    for ln in lines:
        purchasing.add_purchase_line(ctx, pid, ln["part_id"], ln["qty"], money(Decimal(ln["price"])),
                                     supplier_article=ln.get("article") or "", name_on_invoice=ln.get("name") or "")
    with ctx.db.transaction() as s:
        imp = s.get(InvoiceImport, import_id)
        imp.status = "confirmed"
        imp.purchase_id = pid
        imp.confirmed_at = now()
        audit(s, ctx, "invoice.confirm", "invoice_import", import_id, purchase_id=pid)
    return pid


def reject_import(ctx: AppContext, import_id: int, reason: str = "") -> None:
    ctx.require(Perm.IMPORT_INVOICES)
    with ctx.db.transaction() as s:
        imp = get_or_404(s, InvoiceImport, import_id, "Импорт накладной")
        if imp.status == "confirmed":
            raise ValidationError("По импорту уже создана закупка")
        imp.status = "rejected"
        imp.warnings = list(imp.warnings or []) + ([f"Отклонено: {reason}"] if reason else ["Отклонено менеджером"])
        audit(s, ctx, "invoice.reject", "invoice_import", import_id, reason=reason)
