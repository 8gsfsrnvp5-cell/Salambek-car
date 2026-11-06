"""Расписание резервного копирования. Работает, пока запущена программа.

* каждые N часов (по умолчанию 2) — локальный снимок базы;
* ежедневно — копия на внешний диск (если задан и подключён);
* еженедельно — проверка последней копии;
* ежемесячно — напоминание сделать дополнительную внешнюю копию.
Пропущенные задачи (компьютер был выключен) выполняются при следующем запуске.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from autoservice.application.context import AppContext
from autoservice.application.settings_service import get_all, set_system_value
from autoservice.backup import dpapi
from autoservice.backup.service import BackupError, create_backup, latest_backup_file, record_verify, verify_backup

log = logging.getLogger(__name__)


@dataclass
class TickResult:
    done: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reminders: list[str] = field(default_factory=list)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _scheduled_password(settings: dict) -> str | None:
    if not settings.get("backup.encrypt"):
        return None
    token = settings.get("backup.password_dpapi")
    if not token:
        raise BackupError("Включено шифрование, но пароль для копий по расписанию не сохранён — задайте его в настройках")
    return dpapi.unprotect(token)


def tick(ctx: AppContext, now: datetime | None = None) -> TickResult:
    now = now or datetime.now()
    res = TickResult()
    with ctx.db.read() as s:
        settings = get_all(s)
    system_ctx = ctx.as_user(None)

    interval = timedelta(hours=float(settings.get("backup.local_interval_hours") or 2))
    last_local = _parse(settings.get("backup.last_local"))
    if last_local is None or now - last_local >= interval:
        try:
            info = create_backup(system_ctx, ctx.paths.local_backups, kind="auto", keep=int(settings.get("backup.local_keep") or 24),
                                 create_dir=True)
            res.done.append(f"Локальный снимок: {Path(info['path']).name}")
        except BackupError as exc:
            res.warnings.append(f"Локальный снимок не создан: {exc}")
        set_system_value(ctx, "backup.last_local", now.isoformat(timespec="seconds"))

    external = (settings.get("backup.external_path") or "").strip()
    last_ext = _parse(settings.get("backup.last_external"))
    if external and settings.get("backup.external_daily", True) and (last_ext is None or last_ext.date() < now.date()):
        try:
            password = _scheduled_password(settings)
            info = create_backup(system_ctx, Path(external), kind="daily", password=password,
                                 keep=int(settings.get("backup.external_keep") or 30))
            res.done.append(f"Копия на внешний диск: {info['path']}")
            set_system_value(ctx, "backup.last_external", now.isoformat(timespec="seconds"))
        except BackupError as exc:
            res.warnings.append(f"Ежедневная копия на внешний диск не создана: {exc}")
        except Exception as exc:  # noqa: BLE001
            res.warnings.append(f"Ежедневная копия на внешний диск не создана: {exc}")

    last_verify = _parse(settings.get("backup.last_verify"))
    if settings.get("backup.verify_weekly", True) and (last_verify is None or now - last_verify >= timedelta(days=7)):
        latest = latest_backup_file(ctx)
        if latest is not None:
            password = None
            try:
                if latest.name.endswith(".enc"):
                    password = _scheduled_password(settings)
                result = verify_backup(latest, password, ctx.paths.tmp)
                record_verify(ctx, latest, result)
                (res.done if result.ok else res.warnings).append(
                    ("Проверка копии пройдена: " if result.ok else "Проверка копии НЕ пройдена: ") + "; ".join(result.messages)
                )
            except Exception as exc:  # noqa: BLE001
                res.warnings.append(f"Проверка копии не выполнена: {exc}")
            set_system_value(ctx, "backup.last_verify", now.isoformat(timespec="seconds"))

    last_rem = _parse(settings.get("backup.last_monthly_reminder"))
    if settings.get("backup.monthly_reminder", True) and (last_rem is None or now - last_rem >= timedelta(days=30)):
        if last_rem is not None or settings.get("backup.last_local"):
            res.reminders.append("Рекомендуется создать дополнительную копию на другом внешнем носителе и хранить её отдельно.")
        set_system_value(ctx, "backup.last_monthly_reminder", now.isoformat(timespec="seconds"))
    for w in res.warnings:
        log.warning(w)
    return res
