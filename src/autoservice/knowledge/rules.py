"""Правила диагностики: встроенный стартовый набор и правила пользователя.

Встроенные правила — общие определения OBD-II кодов и типовые направления
проверки. Они всегда имеют статус «Требует проверки»: это не данные
производителя конкретного автомобиля.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoservice.application.context import AppContext, audit, get_or_404
from autoservice.config.paths import resource_path
from autoservice.domain.enums import Verification
from autoservice.domain.errors import ValidationError
from autoservice.persistence.models import DiagnosticRule
from autoservice.security.permissions import Perm


def _load_builtin() -> tuple[str, list[dict]]:
    data = json.loads(resource_path("data", "diagnostic_rules.json").read_text(encoding="utf-8"))
    return data["source"], data["rules"]


def seed_builtin_rules(s: Session) -> int:
    source, rules = _load_builtin()
    existing = {r.rule_key: r for r in s.execute(select(DiagnosticRule).where(DiagnosticRule.is_builtin.is_(True))).scalars()}
    changed = 0
    for item in rules:
        row = existing.get(item["key"])
        if row is None:
            row = DiagnosticRule(rule_key=item["key"], is_builtin=True, is_active=True, source=source, title=item["title"])
            s.add(row)
        row.dtc_code = item.get("dtc", "")
        row.title = item["title"]
        row.keywords = ", ".join(item.get("keywords", []))
        row.system_code = item.get("system", "")
        row.possible_causes = item.get("causes", [])
        row.checks = item.get("checks", [])
        row.part_category_codes = item.get("parts", [])
        row.required_info = item.get("info", [])
        row.is_critical = bool(item.get("critical", False))
        row.source = source
        row.verification_status = Verification.NEEDS_CHECK
        changed += 1
    return changed


def rule_dict(r: DiagnosticRule) -> dict:
    return {
        "id": r.id, "key": r.rule_key, "dtc_code": r.dtc_code, "title": r.title, "keywords": r.keywords,
        "system_code": r.system_code, "possible_causes": r.possible_causes or [], "checks": r.checks or [],
        "part_category_codes": r.part_category_codes or [], "required_info": r.required_info or [], "make": r.make,
        "model": r.model, "is_critical": r.is_critical, "source": r.source, "verification_status": r.verification_status,
        "status_title": Verification.TITLES.get(r.verification_status, ""), "is_builtin": r.is_builtin, "is_active": r.is_active,
    }


def list_rules(ctx: AppContext, include_inactive: bool = True) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(DiagnosticRule).order_by(DiagnosticRule.is_builtin, DiagnosticRule.dtc_code, DiagnosticRule.title)
        if not include_inactive:
            stmt = stmt.where(DiagnosticRule.is_active.is_(True))
        return [rule_dict(r) for r in s.execute(stmt).scalars()]


def save_rule(ctx: AppContext, data: dict, rule_id: int | None = None) -> int:
    """Правило пользователя: обязательно источник; «Подтверждено» — только с источником."""
    ctx.require(Perm.LIBRARY_EDIT)
    title = (data.get("title") or "").strip()
    source = (data.get("source") or "").strip()
    if not title:
        raise ValidationError("Укажите название правила")
    if not source:
        raise ValidationError("Укажите источник правила (документ, руководство, проверенный ремонт)")
    status = data.get("verification_status") or Verification.NEEDS_CHECK
    if status not in Verification.TITLES:
        raise ValidationError("Неизвестный статус проверки")

    def as_list(value):
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [x.strip() for x in str(value or "").splitlines() if x.strip()]

    with ctx.db.transaction() as s:
        if rule_id:
            r = get_or_404(s, DiagnosticRule, rule_id, "Правило")
            if r.is_builtin:
                raise ValidationError("Встроенное правило не редактируется — создайте собственное или отключите встроенное")
        else:
            r = DiagnosticRule(title=title, source=source, is_builtin=False)
            s.add(r)
        r.title = title
        r.dtc_code = (data.get("dtc_code") or "").strip().upper()
        r.keywords = (data.get("keywords") or "").strip()
        r.system_code = (data.get("system_code") or "").strip()
        r.possible_causes = as_list(data.get("possible_causes"))
        r.checks = as_list(data.get("checks"))
        r.part_category_codes = as_list(data.get("part_category_codes"))
        r.required_info = as_list(data.get("required_info"))
        r.make = (data.get("make") or "").strip()
        r.model = (data.get("model") or "").strip()
        r.is_critical = bool(data.get("is_critical"))
        r.source = source
        r.verification_status = status
        s.flush()
        audit(s, ctx, "rule.save", "diagnostic_rule", r.id, title=title, status=status)
        return r.id


def set_rule_active(ctx: AppContext, rule_id: int, active: bool) -> None:
    ctx.require(Perm.LIBRARY_EDIT)
    with ctx.db.transaction() as s:
        r = get_or_404(s, DiagnosticRule, rule_id, "Правило")
        r.is_active = active
        audit(s, ctx, "rule.activate" if active else "rule.deactivate", "diagnostic_rule", r.id)


def import_rules(ctx: AppContext, path: Path) -> int:
    """Импорт правил пользователя из JSON (список объектов с полями как в save_rule)."""
    ctx.require(Perm.LIBRARY_EDIT)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Не удалось прочитать файл правил: {exc}") from exc
    items = data.get("rules", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValidationError("Ожидается список правил")
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        save_rule(ctx, item)
        count += 1
    return count
