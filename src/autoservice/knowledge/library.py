"""Библиотека автодела: документы пользователя, заметки, полнотекстовый поиск,
история подтверждённых ремонтов. Защищённые каталоги и руководства в поставку
не входят — пользователь загружает материалы, на которые у него есть права."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from autoservice.application.context import AppContext, audit, get_or_404
from autoservice.application.documents_store import store_file
from autoservice.domain.enums import Verification, WOStatus
from autoservice.domain.errors import ValidationError
from autoservice.domain.normalize import normalize_text
from autoservice.knowledge import extract
from autoservice.persistence.models import KnowledgeSource, PartCategory, Vehicle, WorkOrder
from autoservice.security.permissions import Perm

LIBRARY_EXT = {".pdf", ".txt", ".docx", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
KINDS = {
    "manual": "Руководство / техническая документация",
    "regulation": "Регламент обслуживания",
    "procedure": "Диагностическая процедура",
    "parts_reference": "Справочник запчастей",
    "note": "Заметка мастера",
    "document": "Документ",
}


def _index(s: Session, ks: KnowledgeSource) -> None:
    s.execute(text("DELETE FROM knowledge_fts WHERE rowid = :id"), {"id": ks.id})
    tags = " ".join(x for x in (ks.make, ks.model, ks.engine) if x)
    s.execute(text("INSERT INTO knowledge_fts(rowid, title, body, tags) VALUES (:id, :t, :b, :g)"),
              {"id": ks.id, "t": ks.title, "b": ks.content_text or "", "g": tags})


def _common(data: dict) -> dict:
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError("Укажите название")
    source = (data.get("source") or "").strip()
    if not source:
        raise ValidationError("Укажите источник (издатель, автор, «собственный опыт» и т. п.)")
    status = data.get("verification_status") or Verification.NEEDS_CHECK
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")
    kind = data.get("kind") or "document"
    if kind not in KINDS:
        raise ValidationError("Неизвестный тип материала")
    return {"title": title[:300], "source": source, "verification_status": status, "kind": kind,
            "make": (data.get("make") or "").strip(), "model": (data.get("model") or "").strip(),
            "engine": (data.get("engine") or "").strip(), "category_id": data.get("category_id") or None,
            "vehicle_id": data.get("vehicle_id") or None, "notes": (data.get("notes") or "").strip()}


def add_document(ctx: AppContext, path: Path, data: dict) -> tuple[int, list[str]]:
    ctx.require(Perm.LIBRARY_EDIT)
    fields = _common({"title": data.get("title") or Path(path).stem, **data})
    from autoservice.ai.invoice_import import ocr_status

    status = ocr_status(ctx)
    res = extract.extract_text(Path(path), status)
    with ctx.db.transaction() as s:
        doc = store_file(s, ctx, Path(path), "library", fields["title"], allowed=LIBRARY_EXT,
                         vehicle_id=fields["vehicle_id"])
        ks = KnowledgeSource(document_id=doc.id, content_text=res.text[:2_000_000], created_by=ctx.user_id, **fields)
        s.add(ks)
        s.flush()
        _index(s, ks)
        audit(s, ctx, "library.add_document", "knowledge", ks.id, title=ks.title, engine=res.engine)
        warnings = list(res.warnings)
        if not res.text.strip():
            warnings.append("Текст не извлечён — документ сохранён, но поиск по содержимому недоступен (только по названию)")
        return ks.id, warnings


def add_note(ctx: AppContext, data: dict, body: str) -> int:
    ctx.require(Perm.LIBRARY_EDIT)
    fields = _common({"kind": "note", **data})
    if not (body or "").strip():
        raise ValidationError("Текст заметки пустой")
    with ctx.db.transaction() as s:
        ks = KnowledgeSource(content_text=body.strip(), created_by=ctx.user_id, **fields)
        s.add(ks)
        s.flush()
        _index(s, ks)
        audit(s, ctx, "library.add_note", "knowledge", ks.id, title=ks.title)
        return ks.id


def update_entry(ctx: AppContext, entry_id: int, data: dict, body: str | None = None) -> None:
    ctx.require(Perm.LIBRARY_EDIT)
    with ctx.db.transaction() as s:
        ks = get_or_404(s, KnowledgeSource, entry_id, "Материал")
        fields = _common({**{"title": ks.title, "source": ks.source, "kind": ks.kind, "verification_status": ks.verification_status,
                             "make": ks.make, "model": ks.model, "engine": ks.engine, "category_id": ks.category_id,
                             "vehicle_id": ks.vehicle_id, "notes": ks.notes}, **data})
        for k, v in fields.items():
            setattr(ks, k, v)
        if body is not None and ks.document_id is None:
            ks.content_text = body
        _index(s, ks)
        audit(s, ctx, "library.update", "knowledge", ks.id)


def _fts_query(query: str, mode: str = "AND") -> str:
    tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 2]
    return f" {mode} ".join(f'"{t}"*' for t in tokens[:12])


def entry_dict(ks: KnowledgeSource, cats: dict[int, str], snippet: str = "") -> dict:
    return {"id": ks.id, "title": ks.title, "kind": ks.kind, "kind_title": KINDS.get(ks.kind, ks.kind), "source": ks.source,
            "make": ks.make, "model": ks.model, "engine": ks.engine, "category": cats.get(ks.category_id, ""),
            "category_id": ks.category_id, "vehicle_id": ks.vehicle_id, "status": ks.verification_status,
            "status_title": Verification.TITLES.get(ks.verification_status, ""), "document_id": ks.document_id,
            "snippet": snippet, "created_at": ks.created_at, "notes": ks.notes}


def search(ctx: AppContext, query: str = "", make: str = "", model: str = "", engine: str = "", category_id: int | None = None,
           limit: int = 50) -> list[dict]:
    with ctx.db.read() as s:
        cats = dict(s.execute(select(PartCategory.id, PartCategory.name)).all())
        ids_snippets: list[tuple[int, str]] = []
        if query.strip():
            for mode in ("AND", "OR"):
                q = _fts_query(query, mode)
                if not q:
                    break
                rows = s.execute(text(
                    "SELECT rowid, snippet(knowledge_fts, 1, '«', '»', '…', 14) FROM knowledge_fts "
                    "WHERE knowledge_fts MATCH :q ORDER BY rank LIMIT :lim"), {"q": q, "lim": limit * 3}).all()
                if rows:
                    ids_snippets = [(r[0], r[1]) for r in rows]
                    break
            if not ids_snippets:
                return []
            by_id = {ks.id: ks for ks in s.execute(select(KnowledgeSource).where(KnowledgeSource.id.in_([i for i, _ in ids_snippets]))).scalars()}
            ordered = [(by_id[i], snip) for i, snip in ids_snippets if i in by_id]
        else:
            ordered = [(ks, "") for ks in s.execute(select(KnowledgeSource).order_by(KnowledgeSource.id.desc()).limit(limit * 3)).scalars()]

        def ok(ks: KnowledgeSource) -> bool:
            # Материал без указанной марки — общий, подходит ко всем.
            if make and ks.make and normalize_text(ks.make) != normalize_text(make):
                return False
            if model and ks.model and normalize_text(ks.model) != normalize_text(model):
                return False
            if engine and ks.engine and normalize_text(engine) not in normalize_text(ks.engine):
                return False
            if category_id and ks.category_id and ks.category_id != category_id:
                return False
            return True

        return [entry_dict(ks, cats, snip) for ks, snip in ordered if ok(ks)][:limit]


def get_entry(ctx: AppContext, entry_id: int) -> dict:
    with ctx.db.read() as s:
        ks = get_or_404(s, KnowledgeSource, entry_id, "Материал")
        d = entry_dict(ks, dict(s.execute(select(PartCategory.id, PartCategory.name)).all()))
        d["content_text"] = ks.content_text
        return d


def repair_history(ctx: AppContext, query: str = "", make: str = "", model: str = "", limit: int = 30) -> list[dict]:
    """Подтверждённые ремонты — фактически выполненные и выданные заказ-наряды этого автосервиса."""
    with ctx.db.read() as s:
        return repair_history_s(s, query, make, model, limit)


def repair_history_s(s: Session, query: str = "", make: str = "", model: str = "", limit: int = 30) -> list[dict]:
    stmt = select(WorkOrder).join(Vehicle, Vehicle.id == WorkOrder.vehicle_id).where(WorkOrder.status == WOStatus.ISSUED)
    if make:
        stmt = stmt.where(Vehicle.make.ilike(make.strip()))
    if model:
        stmt = stmt.where(Vehicle.model.ilike(model.strip()))
    tokens = [t for t in re.findall(r"\w+", normalize_text(query)) if len(t) >= 3]
    out = []
    for wo in s.execute(stmt.order_by(WorkOrder.closed_at.desc()).limit(2000)).scalars():
        hay = normalize_text(" ".join([wo.complaint, wo.diagnosis, wo.recommendations] + [x.name for x in wo.services if x.is_done]
                                      + [p.part.name for p in wo.parts if p.qty > 0]))
        score = sum(1 for t in tokens if t in hay) if tokens else 1
        if tokens and score == 0:
            continue
        out.append({"work_order_id": wo.id, "number": wo.number, "closed_at": wo.closed_at,
                    "vehicle": f"{wo.vehicle.make} {wo.vehicle.model} {wo.vehicle.year or ''}".strip(),
                    "engine_code": wo.vehicle.engine_code, "mileage": wo.mileage, "complaint": wo.complaint, "diagnosis": wo.diagnosis,
                    "services": [x.name for x in wo.services if x.is_done],
                    "parts": [f"{p.part.name} ({p.part.oem_number})" if p.part.oem_number else p.part.name for p in wo.parts if p.qty > 0],
                    "score": score})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]


def list_entries(ctx: AppContext, limit: int = 500) -> list[dict]:
    return search(ctx, "", limit=limit)


def any_entries(s: Session) -> bool:
    return s.execute(select(KnowledgeSource.id).limit(1)).first() is not None


__all__ = ["add_document", "add_note", "update_entry", "search", "get_entry", "repair_history", "KINDS", "or_"]
