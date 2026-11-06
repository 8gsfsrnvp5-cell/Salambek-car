"""Контекст выполнения операций: база, каталоги, текущий пользователь."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoservice.config.paths import AppPaths
from autoservice.domain.errors import NotFound, PermissionDenied
from autoservice.persistence.db import Database
from autoservice.persistence.models import AuditLog, Sequence, now
from autoservice.security.permissions import PERM_TITLES, has_perm


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    full_name: str
    role: str


@dataclass(frozen=True)
class AppContext:
    db: Database
    paths: AppPaths
    user: CurrentUser | None = None

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None

    def can(self, perm: str) -> bool:
        return self.user is not None and has_perm(self.user.role, perm)

    def require(self, perm: str) -> None:
        if self.user is None:
            raise PermissionDenied("Требуется вход в программу")
        if not has_perm(self.user.role, perm):
            title = PERM_TITLES.get(perm, perm)
            raise PermissionDenied(f"Недостаточно прав: {title}. Обратитесь к администратору.")

    def as_user(self, user: CurrentUser | None) -> "AppContext":
        return replace(self, user=user)


def audit(s: Session, ctx: AppContext, action: str, entity: str = "", entity_id: int | None = None, **details) -> None:
    s.add(
        AuditLog(
            ts=now(),
            user_id=ctx.user_id,
            username=ctx.user.username if ctx.user else "система",
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details or None,
        )
    )


def next_number(s: Session, name: str, prefix: str = "", width: int = 6) -> str:
    seq = s.get(Sequence, name)
    if seq is None:
        seq = Sequence(name=name, next_value=1)
        s.add(seq)
        s.flush()
    value = seq.next_value
    seq.next_value = value + 1
    s.flush()
    return f"{prefix}{value:0{width}d}"


def get_or_404(s: Session, model, obj_id, what: str = "Запись"):
    obj = s.get(model, obj_id) if obj_id is not None else None
    if obj is None:
        raise NotFound(f"{what} не найдена (№ {obj_id})")
    return obj


def today() -> date:
    return date.today()


def as_date(value) -> date:
    if value is None:
        return today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%d.%m.%Y").date() if "." in str(value) else date.fromisoformat(str(value))


def first_or_none(s: Session, stmt):
    return s.execute(stmt).scalars().first()


__all__ = ["AppContext", "CurrentUser", "audit", "next_number", "get_or_404", "today", "as_date", "select", "first_or_none"]
