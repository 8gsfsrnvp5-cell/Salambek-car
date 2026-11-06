"""Хранилище вложений. Оригиналы копируются в каталог данных и не изменяются."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoservice.application.context import AppContext, audit, get_or_404
from autoservice.persistence.models import Document
from autoservice.security.safe_paths import check_import_file, ensure_inside, safe_filename

KIND_TITLES = {
    "invoice": "Накладная / счёт поставщика",
    "library": "Документ библиотеки",
    "photo": "Фотография",
    "vehicle": "Документ автомобиля",
    "attachment": "Вложение",
    "logo": "Логотип",
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def store_file(s: Session, ctx: AppContext, src: Path, kind: str, title: str = "", note: str = "", allowed: set[str] | None = None,
               **links) -> Document:
    src = check_import_file(Path(src), allowed)
    digest = sha256_of(src)
    folder = ctx.paths.attachments / kind / datetime.now().strftime("%Y") / datetime.now().strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    dest = ensure_inside(ctx.paths.attachments, folder / f"{digest[:12]}_{safe_filename(src.name)}")
    if not dest.exists():
        shutil.copy2(src, dest)
    doc = Document(
        kind=kind,
        title=(title or src.stem)[:300],
        original_name=src.name[:300],
        stored_path=dest.relative_to(ctx.paths.attachments).as_posix(),
        sha256=digest,
        size_bytes=src.stat().st_size,
        mime=mimetypes.guess_type(src.name)[0] or "",
        note=note,
        created_by=ctx.user_id,
        **links,
    )
    s.add(doc)
    s.flush()
    audit(s, ctx, "document.store", "document", doc.id, kind=kind, name=src.name, sha256=digest)
    return doc


def add_attachment(ctx: AppContext, src: Path, kind: str = "attachment", title: str = "", **links) -> int:
    with ctx.db.transaction() as s:
        return store_file(s, ctx, src, kind, title, **links).id


def document_file(ctx: AppContext, document_id: int) -> Path:
    with ctx.db.read() as s:
        doc = get_or_404(s, Document, document_id, "Документ")
        return ensure_inside(ctx.paths.attachments, ctx.paths.attachments / doc.stored_path)


def find_by_sha(s: Session, digest: str, kind: str | None = None) -> list[Document]:
    stmt = select(Document).where(Document.sha256 == digest)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    return list(s.execute(stmt.order_by(Document.id)).scalars())


def list_documents(ctx: AppContext, kind: str | None = None, query: str = "", limit: int = 500, **links) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Document).order_by(Document.id.desc()).limit(limit)
        if kind:
            stmt = stmt.where(Document.kind == kind)
        for key, value in links.items():
            if value:
                stmt = stmt.where(getattr(Document, key) == value)
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(func.lower(Document.title).like(like) | func.lower(Document.original_name).like(like))
        return [
            {"id": d.id, "kind": d.kind, "kind_title": KIND_TITLES.get(d.kind, d.kind), "title": d.title,
             "original_name": d.original_name, "size_bytes": d.size_bytes, "created_at": d.created_at, "sha256": d.sha256,
             "path": str(ctx.paths.attachments / d.stored_path), "vehicle_id": d.vehicle_id, "purchase_id": d.purchase_id,
             "work_order_id": d.work_order_id, "part_id": d.part_id}
            for d in s.execute(stmt).scalars()
        ]
