"""Каталог запчастей, артикулы, аналоги, применимость, поставщики."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from autoservice.application import inventory
from autoservice.application.context import AppContext, audit, get_or_404, next_number
from autoservice.application.settings_service import default_markup
from autoservice.config.paths import resource_path
from autoservice.domain.enums import UNITS, Verification
from autoservice.domain.errors import ValidationError
from autoservice.domain.markup import price_from_cost, resolve_markup, validate_markup
from autoservice.domain.money import D, ZERO, qty as to_qty
from autoservice.domain.normalize import normalize_article, normalize_text
from autoservice.persistence.models import (
    Part,
    PartAnalog,
    PartCategory,
    PartCompatibility,
    PartNumber,
    PartSupplier,
    PriceHistory,
    Supplier,
)
from autoservice.security.permissions import Perm

# --------------------------------------------------------------------------- категории


def seed_categories(s: Session) -> int:
    data = json.loads(resource_path("data", "part_categories.json").read_text(encoding="utf-8"))
    existing = {c.code for c in s.execute(select(PartCategory)).scalars()}
    added = 0
    for order, item in enumerate(data):
        if item["code"] in existing:
            continue
        s.add(PartCategory(code=item["code"], name=item["name"], sort_order=order, is_critical=item.get("critical", False)))
        added += 1
    return added


def list_categories(ctx: AppContext) -> list[dict]:
    with ctx.db.read() as s:
        cats = s.execute(select(PartCategory).order_by(PartCategory.sort_order, PartCategory.name)).scalars().all()
        counts = dict(
            s.execute(select(Part.category_id, func.count()).where(Part.is_active.is_(True)).group_by(Part.category_id)).all()
        )
        return [
            {
                "id": c.id,
                "code": c.code,
                "name": c.name,
                "parent_id": c.parent_id,
                "is_critical": c.is_critical,
                "parts_count": counts.get(c.id, 0),
            }
            for c in cats
        ]


def add_category(ctx: AppContext, name: str, parent_id: int | None = None) -> int:
    ctx.require(Perm.CATALOG_EDIT)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Укажите название категории")
    with ctx.db.transaction() as s:
        code = "user_" + next_number(s, "category", "", 4)
        cat = PartCategory(code=code, name=name, parent_id=parent_id, sort_order=1000)
        s.add(cat)
        s.flush()
        audit(s, ctx, "category.create", "part_category", cat.id, name=name)
        return cat.id


def category_by_code(s: Session, code: str) -> PartCategory | None:
    return s.execute(select(PartCategory).where(PartCategory.code == code)).scalar_one_or_none()


# --------------------------------------------------------------------------- запчасти

PART_FIELDS = ("name", "category_id", "manufacturer", "brand", "oem_number", "unit", "min_stock", "location", "description")


def _clean_part_data(data: dict) -> dict:
    out = {}
    for key in PART_FIELDS:
        if key in data:
            out[key] = data[key]
    if "name" in out:
        out["name"] = (out["name"] or "").strip()
        if not out["name"]:
            raise ValidationError("Укажите наименование запчасти")
    if "unit" in out:
        out["unit"] = (out["unit"] or "шт").strip()
        if out["unit"] not in UNITS:
            raise ValidationError(f"Единица измерения должна быть одной из: {', '.join(UNITS)}")
    if "min_stock" in out:
        out["min_stock"] = to_qty(out["min_stock"] if out["min_stock"] not in (None, "") else "0")
        if out["min_stock"] < 0:
            raise ValidationError("Минимальный остаток не может быть отрицательным")
    for key in ("manufacturer", "brand", "oem_number", "location", "description"):
        if key in out:
            out[key] = (out[key] or "").strip()
    return out


def create_part(ctx: AppContext, data: dict) -> int:
    ctx.require(Perm.CATALOG_EDIT)
    clean = _clean_part_data({"unit": "шт", **data})
    if "name" not in clean:
        raise ValidationError("Укажите наименование запчасти")
    markup = data.get("markup_pct")
    markup = None if markup in (None, "") else validate_markup(markup)
    with ctx.db.transaction() as s:
        if clean.get("category_id"):
            get_or_404(s, PartCategory, clean["category_id"], "Категория")
        part = Part(code=next_number(s, "part", "P-", 6), **clean, markup_pct=markup)
        part.name_search = normalize_text(part.name)
        part.oem_number_normalized = normalize_article(part.oem_number)
        s.add(part)
        s.flush()
        if markup is not None:
            s.add(PriceHistory(part_id=part.id, kind="markup", percent=markup, source_doc="карточка", user_id=ctx.user_id))
        audit(s, ctx, "part.create", "part", part.id, name=part.name, oem=part.oem_number)
        return part.id


def update_part(ctx: AppContext, part_id: int, data: dict) -> None:
    ctx.require(Perm.CATALOG_EDIT)
    clean = _clean_part_data(data)
    with ctx.db.transaction() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        changes = {}
        for key, value in clean.items():
            if getattr(part, key) != value:
                changes[key] = [str(getattr(part, key)), str(value)]
                setattr(part, key, value)
        if "markup_pct" in data:
            new = None if data["markup_pct"] in (None, "") else validate_markup(data["markup_pct"])
            if new != part.markup_pct:
                changes["markup_pct"] = [str(part.markup_pct), str(new)]
                part.markup_pct = new
                s.add(PriceHistory(part_id=part.id, kind="markup", percent=new, source_doc="карточка", user_id=ctx.user_id))
        if "is_active" in data:
            part.is_active = bool(data["is_active"])
        part.name_search = normalize_text(part.name)
        part.oem_number_normalized = normalize_article(part.oem_number)
        if changes:
            audit(s, ctx, "part.update", "part", part.id, changes=changes)


def set_part_photo(ctx: AppContext, part_id: int, document_id: int | None) -> None:
    ctx.require(Perm.CATALOG_EDIT)
    with ctx.db.transaction() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        part.photo_document_id = document_id
        audit(s, ctx, "part.photo", "part", part_id, document_id=document_id)


def add_part_number(
    ctx: AppContext, part_id: int, number: str, brand: str = "", kind: str = "other", source: str = "", status: str = Verification.NEEDS_CHECK
) -> int:
    ctx.require(Perm.CATALOG_EDIT)
    norm = normalize_article(number)
    if not norm:
        raise ValidationError("Укажите артикул")
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")
    if status == Verification.CONFIRMED and not (source or "").strip():
        raise ValidationError("Для подтверждённого артикула обязательно укажите источник")
    with ctx.db.transaction() as s:
        get_or_404(s, Part, part_id, "Запчасть")
        exists = s.execute(
            select(PartNumber).where(PartNumber.part_id == part_id, PartNumber.number_normalized == norm, PartNumber.brand == (brand or "").strip())
        ).first()
        if exists:
            raise ValidationError("Такой артикул уже добавлен")
        pn = PartNumber(
            part_id=part_id, number=number.strip(), number_normalized=norm, brand=(brand or "").strip(), kind=kind,
            source=(source or "").strip(), verification_status=status,
        )
        s.add(pn)
        s.flush()
        audit(s, ctx, "part.number_add", "part", part_id, number=number, status=status)
        return pn.id


def add_analog(ctx: AppContext, part_id: int, analog_part_id: int, source: str, status: str = Verification.NEEDS_CHECK, note: str = "") -> int:
    """Аналоги не определяются по похожему названию — только явная связь с источником."""
    ctx.require(Perm.CATALOG_EDIT)
    if part_id == analog_part_id:
        raise ValidationError("Запчасть не может быть аналогом самой себя")
    if not (source or "").strip():
        raise ValidationError("Укажите источник, подтверждающий взаимозаменяемость")
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")
    with ctx.db.transaction() as s:
        get_or_404(s, Part, part_id, "Запчасть")
        get_or_404(s, Part, analog_part_id, "Запчасть-аналог")
        ids = []
        for a, b in ((part_id, analog_part_id), (analog_part_id, part_id)):
            if s.execute(select(PartAnalog).where(PartAnalog.part_id == a, PartAnalog.analog_part_id == b)).first():
                continue
            link = PartAnalog(part_id=a, analog_part_id=b, source=source.strip(), verification_status=status, note=note)
            s.add(link)
            s.flush()
            ids.append(link.id)
        audit(s, ctx, "part.analog_add", "part", part_id, analog=analog_part_id, status=status, source=source)
        return ids[0] if ids else 0


def add_compatibility(ctx: AppContext, part_id: int, data: dict) -> int:
    """Применимость всегда сохраняется с источником и статусом проверки."""
    ctx.require(Perm.CATALOG_EDIT)
    make = (data.get("make") or "").strip()
    source = (data.get("source") or "").strip()
    status = data.get("verification_status") or Verification.NEEDS_CHECK
    if not make:
        raise ValidationError("Укажите марку автомобиля")
    if not source:
        raise ValidationError("Укажите источник сведений о применимости (каталог, документ, проверенная установка)")
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")
    year_from = int(data["year_from"]) if data.get("year_from") not in (None, "") else None
    year_to = int(data["year_to"]) if data.get("year_to") not in (None, "") else None
    if year_from and year_to and year_from > year_to:
        raise ValidationError("Год «с» больше года «по»")
    volume = data.get("engine_volume")
    with ctx.db.transaction() as s:
        get_or_404(s, Part, part_id, "Запчасть")
        row = PartCompatibility(
            part_id=part_id,
            make=make,
            model=(data.get("model") or "").strip(),
            generation=(data.get("generation") or "").strip(),
            year_from=year_from,
            year_to=year_to,
            engine_code=(data.get("engine_code") or "").strip(),
            engine_volume=to_qty(volume) if volume not in (None, "") else None,
            fuel=(data.get("fuel") or "").strip(),
            note=(data.get("note") or "").strip(),
            source=source,
            verification_status=status,
            created_by=ctx.user_id,
        )
        s.add(row)
        s.flush()
        audit(s, ctx, "part.compat_add", "part", part_id, make=make, model=row.model, status=status, source=source)
        return row.id


def set_compatibility_status(ctx: AppContext, compat_id: int, status: str, source: str | None = None) -> None:
    ctx.require(Perm.CATALOG_EDIT)
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")
    with ctx.db.transaction() as s:
        row = get_or_404(s, PartCompatibility, compat_id, "Запись применимости")
        if source:
            row.source = source.strip()
        row.verification_status = status
        audit(s, ctx, "part.compat_status", "part", row.part_id, compat_id=compat_id, status=status)


def remove_compatibility(ctx: AppContext, compat_id: int) -> None:
    ctx.require(Perm.CATALOG_EDIT)
    with ctx.db.transaction() as s:
        row = get_or_404(s, PartCompatibility, compat_id, "Запись применимости")
        audit(s, ctx, "part.compat_remove", "part", row.part_id, make=row.make, model=row.model, source=row.source)
        s.delete(row)


def vehicle_matches(c: PartCompatibility, make: str, model: str = "", year: int | None = None, engine_code: str = "") -> bool:
    if normalize_text(c.make) != normalize_text(make):
        return False
    if c.model and model and normalize_text(c.model) != normalize_text(model):
        return False
    if c.model and not model:
        return False
    if year is not None:
        if c.year_from and year < c.year_from:
            return False
        if c.year_to and year > c.year_to:
            return False
    if c.engine_code and engine_code and normalize_article(c.engine_code) != normalize_article(engine_code):
        return False
    return True


def compatible_parts(s: Session, make: str, model: str = "", year: int | None = None, engine_code: str = "", category_codes: list[str] | None = None) -> list[tuple[Part, PartCompatibility]]:
    stmt = select(PartCompatibility, Part).join(Part, Part.id == PartCompatibility.part_id).where(
        func.lower(PartCompatibility.make) == make.strip().lower(), Part.is_active.is_(True)
    )
    if category_codes:
        stmt = stmt.join(PartCategory, PartCategory.id == Part.category_id).where(PartCategory.code.in_(category_codes))
    out = []
    for compat, part in s.execute(stmt).all():
        if vehicle_matches(compat, make, model, year, engine_code):
            out.append((part, compat))
    return out


# --------------------------------------------------------------------------- поиск и карточка


def price_info(s: Session, part: Part, qty: Decimal = Decimal("1")) -> dict:
    prev = inventory.preview_cost(s, part.id, qty)
    res = resolve_markup(batch=prev.first_batch_markup, part=part.markup_pct, default=default_markup(s))
    price = None
    if prev.unit_cost is not None and res.percent is not None:
        price = price_from_cost(prev.unit_cost, res.percent)
    return {
        "unit_cost": prev.unit_cost,
        "cost_estimated": prev.estimated,
        "markup_pct": res.percent,
        "markup_source": res.source,
        "markup_title": res.title,
        "unit_price": price,
    }


def _part_row(s: Session, part: Part, summary: dict | None, cats: dict[int, str]) -> dict:
    st = summary or {"on_hand": ZERO, "reserved": ZERO, "available": ZERO, "value": ZERO, "unknown_qty": ZERO}
    row = {
        "id": part.id,
        "code": part.code,
        "name": part.name,
        "category_id": part.category_id,
        "category": cats.get(part.category_id, ""),
        "brand": part.brand,
        "manufacturer": part.manufacturer,
        "oem_number": part.oem_number,
        "unit": part.unit,
        "location": part.location,
        "min_stock": part.min_stock,
        "markup_pct": part.markup_pct,
        "is_active": part.is_active,
        "on_hand": st["on_hand"],
        "reserved": st["reserved"],
        "available": st["available"],
        "stock_value": st["value"],
        "unknown_cost_qty": st["unknown_qty"],
        "low_stock": part.min_stock > 0 and st["available"] < part.min_stock,
    }
    row.update(price_info(s, part))
    return row


def search_parts(
    ctx: AppContext,
    query: str = "",
    category_id: int | None = None,
    only_in_stock: bool = False,
    include_inactive: bool = False,
    limit: int = 500,
) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Part)
        if not include_inactive:
            stmt = stmt.where(Part.is_active.is_(True))
        if category_id:
            stmt = stmt.where(Part.category_id == category_id)
        q = (query or "").strip()
        if q:
            norm = normalize_article(q)
            text = f"%{normalize_text(q)}%"
            by_number = select(PartNumber.part_id).where(PartNumber.number_normalized.like(f"%{norm}%")) if norm else None
            by_supplier = (
                select(PartSupplier.part_id).where(PartSupplier.supplier_article_normalized.like(f"%{norm}%")) if norm else None
            )
            conds = [Part.name_search.like(text), func.lower(Part.brand).like(text), func.lower(Part.code).like(text)]
            if norm:
                conds += [Part.oem_number_normalized.like(f"%{norm}%"), Part.id.in_(by_number), Part.id.in_(by_supplier)]
            stmt = stmt.where(or_(*conds))
        parts = s.execute(stmt.order_by(Part.name).limit(limit)).scalars().all()
        summary = inventory.stock_summary(s)
        cats = dict(s.execute(select(PartCategory.id, PartCategory.name)).all())
        rows = [_part_row(s, p, summary.get(p.id), cats) for p in parts]
        if only_in_stock:
            rows = [r for r in rows if r["on_hand"] > 0]
        return rows


def get_part(ctx: AppContext, part_id: int) -> dict:
    with ctx.db.read() as s:
        part = get_or_404(s, Part, part_id, "Запчасть")
        cats = dict(s.execute(select(PartCategory.id, PartCategory.name)).all())
        row = _part_row(s, part, inventory.stock_summary(s).get(part.id), cats)
        row["description"] = part.description
        row["photo_document_id"] = part.photo_document_id
        row["numbers"] = [
            {"id": n.id, "number": n.number, "brand": n.brand, "kind": n.kind, "source": n.source,
             "status": n.verification_status, "status_title": Verification.TITLES.get(n.verification_status, "")}
            for n in s.execute(select(PartNumber).where(PartNumber.part_id == part_id)).scalars()
        ]
        analogs = []
        for link, ap in s.execute(
            select(PartAnalog, Part).join(Part, Part.id == PartAnalog.analog_part_id).where(PartAnalog.part_id == part_id)
        ).all():
            analogs.append({"id": link.id, "part_id": ap.id, "code": ap.code, "name": ap.name, "brand": ap.brand,
                            "oem_number": ap.oem_number, "source": link.source, "status": link.verification_status,
                            "status_title": Verification.TITLES.get(link.verification_status, "")})
        row["analogs"] = analogs
        row["compatibility"] = [
            {"id": c.id, "make": c.make, "model": c.model, "generation": c.generation, "year_from": c.year_from,
             "year_to": c.year_to, "engine_code": c.engine_code, "engine_volume": c.engine_volume, "fuel": c.fuel,
             "note": c.note, "source": c.source, "status": c.verification_status,
             "status_title": Verification.TITLES.get(c.verification_status, "")}
            for c in s.execute(select(PartCompatibility).where(PartCompatibility.part_id == part_id)).scalars()
        ]
        row["suppliers"] = [
            {"id": ps.id, "supplier_id": sup.id, "supplier": sup.name, "supplier_article": ps.supplier_article,
             "last_price": ps.last_price, "last_price_date": ps.last_price_date, "lead_time_days": ps.lead_time_days or sup.lead_time_days}
            for ps, sup in s.execute(
                select(PartSupplier, Supplier).join(Supplier, Supplier.id == PartSupplier.supplier_id).where(PartSupplier.part_id == part_id)
            ).all()
        ]
        row["price_history"] = [
            {"ts": h.ts, "kind": h.kind, "amount": h.amount, "percent": h.percent, "source_doc": h.source_doc}
            for h in s.execute(select(PriceHistory).where(PriceHistory.part_id == part_id).order_by(PriceHistory.ts.desc(), PriceHistory.id.desc())).scalars()
        ]
        return row


def find_parts_by_article(s: Session, article: str, supplier_id: int | None = None) -> list[Part]:
    norm = normalize_article(article)
    if not norm:
        return []
    ids: set[int] = set()
    ids.update(s.execute(select(Part.id).where(Part.oem_number_normalized == norm, Part.is_active.is_(True))).scalars())
    ids.update(s.execute(select(PartNumber.part_id).where(PartNumber.number_normalized == norm)).scalars())
    stmt = select(PartSupplier.part_id).where(PartSupplier.supplier_article_normalized == norm)
    if supplier_id:
        stmt = stmt.where(PartSupplier.supplier_id == supplier_id)
    ids.update(s.execute(stmt).scalars())
    if not ids:
        return []
    return list(s.execute(select(Part).where(Part.id.in_(ids), Part.is_active.is_(True))).scalars())


# --------------------------------------------------------------------------- поставщики


def save_supplier(ctx: AppContext, data: dict, supplier_id: int | None = None) -> int:
    ctx.require(Perm.PURCHASE_EDIT)
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Укажите название поставщика")
    inn = (data.get("inn") or "").strip()
    if inn and (not inn.isdigit() or len(inn) not in (10, 12)):
        raise ValidationError("ИНН должен содержать 10 или 12 цифр")
    lead = data.get("lead_time_days")
    lead = int(lead) if lead not in (None, "") else None
    if lead is not None and lead < 0:
        raise ValidationError("Срок поставки не может быть отрицательным")
    with ctx.db.transaction() as s:
        if supplier_id:
            sup = get_or_404(s, Supplier, supplier_id, "Поставщик")
        else:
            sup = Supplier(name=name)
            s.add(sup)
        sup.name = name
        sup.inn = inn
        for key in ("phone", "email", "address", "contact_person", "notes"):
            if key in data:
                setattr(sup, key, (data.get(key) or "").strip())
        sup.lead_time_days = lead
        if "is_active" in data:
            sup.is_active = bool(data["is_active"])
        s.flush()
        audit(s, ctx, "supplier.update" if supplier_id else "supplier.create", "supplier", sup.id, name=name)
        return sup.id


def list_suppliers(ctx: AppContext, include_inactive: bool = False) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Supplier).order_by(Supplier.name)
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active.is_(True))
        return [
            {"id": x.id, "name": x.name, "inn": x.inn, "phone": x.phone, "email": x.email, "address": x.address,
             "contact_person": x.contact_person, "lead_time_days": x.lead_time_days, "notes": x.notes, "is_active": x.is_active}
            for x in s.execute(stmt).scalars()
        ]


def link_supplier(s: Session, part_id: int, supplier_id: int, article: str = "", price: Decimal | None = None, price_date: date | None = None) -> PartSupplier:
    norm = normalize_article(article)
    link = s.execute(
        select(PartSupplier).where(
            PartSupplier.part_id == part_id, PartSupplier.supplier_id == supplier_id, PartSupplier.supplier_article_normalized == norm
        )
    ).scalar_one_or_none()
    if link is None:
        link = PartSupplier(part_id=part_id, supplier_id=supplier_id, supplier_article=(article or "").strip(), supplier_article_normalized=norm)
        s.add(link)
    if price is not None:
        link.last_price = price
        link.last_price_date = price_date
    s.flush()
    return link


# --------------------------------------------------------------------------- импорт справочника

IMPORT_COLUMNS = {
    "name": ["наименование", "название", "товар", "name"],
    "oem_number": ["артикул", "оригинальный артикул", "oem", "код производителя", "номер"],
    "brand": ["бренд", "марка детали", "brand"],
    "manufacturer": ["производитель", "manufacturer"],
    "category": ["категория", "группа", "category"],
    "unit": ["ед", "ед.", "единица", "ед. изм.", "unit"],
    "min_stock": ["мин. остаток", "минимальный остаток", "мин остаток"],
    "location": ["место хранения", "ячейка", "location"],
    "alt_numbers": ["альтернативные артикулы", "аналоги артикулы", "кроссы"],
    "source": ["источник", "source"],
}


def _read_table(path: Path) -> list[list[str]]:
    ext = path.suffix.lower()
    if ext == ".csv":
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValidationError("Не удалось определить кодировку CSV (ожидается UTF-8 или Windows-1251)")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t") if text.strip() else csv.excel
        return [list(r) for r in csv.reader(text.splitlines(), dialect)]
    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            return [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    raise ValidationError("Поддерживаются файлы CSV и XLSX")


def _map_header(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, title in enumerate(header):
        t = normalize_text(title).strip(" :")
        for key, names in IMPORT_COLUMNS.items():
            if key not in mapping and t in names:
                mapping[key] = idx
    return mapping


def import_catalog(ctx: AppContext, path: Path) -> dict:
    """Импорт справочника из CSV/XLSX. Применимость при импорте не создаётся —
    только карточки и артикулы, альтернативные артикулы получают статус
    «Требует проверки», если в строке не указан источник."""
    ctx.require(Perm.CATALOG_EDIT)
    path = Path(path)
    rows = _read_table(path)
    if not rows:
        raise ValidationError("Файл пустой")
    mapping = _map_header(rows[0])
    if "name" not in mapping:
        raise ValidationError("В первой строке не найден столбец «Наименование»")
    report = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    with ctx.db.transaction() as s:
        cats = {normalize_text(c.name): c.id for c in s.execute(select(PartCategory)).scalars()}
        for line_no, row in enumerate(rows[1:], start=2):
            def cell(key):
                i = mapping.get(key)
                return (row[i] if i is not None and i < len(row) else "").strip()

            name = cell("name")
            if not name:
                report["skipped"] += 1
                continue
            try:
                with s.begin_nested():
                    oem = cell("oem_number")
                    brand = cell("brand")
                    unit = cell("unit") or "шт"
                    if unit not in UNITS:
                        unit = "шт"
                    min_stock = to_qty(cell("min_stock").replace(",", ".")) if cell("min_stock") else Decimal("0")
                    cat_id = cats.get(normalize_text(cell("category"))) if cell("category") else None
                    existing = None
                    if oem:
                        existing = s.execute(
                            select(Part).where(Part.oem_number_normalized == normalize_article(oem), Part.brand == brand)
                        ).scalars().first()
                    if existing:
                        existing.name = name
                        existing.name_search = normalize_text(name)
                        if cell("manufacturer"):
                            existing.manufacturer = cell("manufacturer")
                        if cat_id:
                            existing.category_id = cat_id
                        if cell("location"):
                            existing.location = cell("location")
                        part = existing
                        report["updated"] += 1
                    else:
                        part = Part(
                            code=next_number(s, "part", "P-", 6), name=name, name_search=normalize_text(name), brand=brand,
                            manufacturer=cell("manufacturer"), oem_number=oem, oem_number_normalized=normalize_article(oem),
                            unit=unit, min_stock=min_stock, location=cell("location"), category_id=cat_id,
                        )
                        s.add(part)
                        s.flush()
                        report["created"] += 1
                    source = cell("source") or f"Импорт из файла {path.name}"
                    for alt in [a.strip() for a in cell("alt_numbers").replace(";", ",").split(",") if a.strip()]:
                        norm = normalize_article(alt)
                        if s.execute(select(PartNumber).where(PartNumber.part_id == part.id, PartNumber.number_normalized == norm)).first():
                            continue
                        s.add(PartNumber(part_id=part.id, number=alt, number_normalized=norm, kind="other", source=source,
                                         verification_status=Verification.NEEDS_CHECK))
            except Exception as exc:  # noqa: BLE001 — ошибка строки не должна прерывать импорт
                report["errors"].append(f"Строка {line_no}: {exc}")
        audit(s, ctx, "catalog.import", "part", None, file=path.name, **{k: v for k, v in report.items() if k != "errors"},
              errors=len(report["errors"]))
    return report


def parse_decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return D(value)
