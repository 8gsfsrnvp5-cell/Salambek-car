"""Просмотр журнала действий (только администратор)."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from autoservice.application.context import AppContext
from autoservice.persistence.models import AuditLog
from autoservice.security.permissions import Perm

ACTION_TITLES = {
    "auth.login": "Вход", "auth.failed": "Неудачный вход", "settings.update": "Изменение настроек",
    "purchase.post": "Проведение закупки", "purchase.cancel": "Отмена закупки", "sale.post": "Проведение продажи",
    "sale.cancel": "Отмена продажи", "sale.return": "Возврат от покупателя", "payment.create": "Платёж",
    "payment.cancel": "Отмена платежа", "work_order.status": "Смена статуса заказ-наряда", "backup.create": "Резервная копия",
    "backup.restore_start": "Восстановление из копии", "invoice.import": "Импорт накладной", "invoice.confirm": "Подтверждение накладной",
}


def list_audit(ctx: AppContext, query: str = "", limit: int = 1000) -> list[dict]:
    ctx.require(Perm.AUDIT_VIEW)
    with ctx.db.read() as s:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(or_(func.lower(AuditLog.action).like(like), func.lower(AuditLog.username).like(like),
                                  func.lower(AuditLog.entity).like(like)))
        return [
            {"ts": a.ts, "username": a.username, "action": ACTION_TITLES.get(a.action, a.action), "entity": a.entity,
             "entity_id": a.entity_id, "details": ", ".join(f"{k}: {v}" for k, v in (a.details or {}).items())[:300]}
            for a in s.execute(stmt).scalars()
        ]
