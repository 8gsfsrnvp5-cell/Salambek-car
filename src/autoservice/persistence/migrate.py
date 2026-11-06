"""Миграции схемы базы с резервной копией и откатом при неудаче."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from autoservice.config.paths import resource_path
from autoservice.domain.errors import DomainError

log = logging.getLogger(__name__)

MIGRATIONS_DIR = resource_path("persistence", "migrations")


class MigrationError(DomainError):
    pass


@dataclass
class MigrationReport:
    created: bool
    from_revision: str | None
    to_revision: str | None
    backup_file: Path | None = None

    @property
    def upgraded(self) -> bool:
        return self.from_revision != self.to_revision


def alembic_config(db_file: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_file).as_posix()}")
    return cfg


def script_directory() -> ScriptDirectory:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg)


def head_revision() -> str:
    return script_directory().get_current_head()


def known_revisions() -> set[str]:
    return {rev.revision for rev in script_directory().walk_revisions()}


def current_revision(db_file: Path) -> str | None:
    engine = create_engine(f"sqlite:///{Path(db_file).as_posix()}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _has_tables(db_file: Path) -> bool:
    con = sqlite3.connect(db_file)
    try:
        rows = con.execute("select count(*) from sqlite_master where type='table'").fetchone()
        return bool(rows and rows[0])
    finally:
        con.close()


def sqlite_backup(src: Path, dest: Path) -> Path:
    """Безопасная копия работающей базы через SQLite backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return dest


def _restore_file(backup_file: Path, db_file: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = db_file.with_name(db_file.name + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(backup_file, db_file)


def upgrade(db_file: Path, revision: str = "head") -> None:
    command.upgrade(alembic_config(db_file), revision)


def prepare_database(db_file: Path, backup_dir: Path) -> MigrationReport:
    """Создаёт базу при первом запуске или обновляет схему существующей.

    Перед обновлением делается копия базы; если миграция не удалась, база
    возвращается к исходному состоянию и выдаётся понятная ошибка.
    """
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    head = head_revision()

    if not db_file.exists() or not _has_tables(db_file):
        upgrade(db_file)
        return MigrationReport(created=True, from_revision=None, to_revision=current_revision(db_file))

    current = current_revision(db_file)
    if current is None:
        raise MigrationError(
            "Файл базы не является базой AutoService AI (нет версии схемы). Работа остановлена, файл не изменён."
        )
    if current == head:
        return MigrationReport(created=False, from_revision=current, to_revision=current)
    if current not in known_revisions():
        raise MigrationError(
            "База создана более новой версией программы. Установите версию не ниже той, "
            "в которой база была создана. Данные не изменены."
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_file = sqlite_backup(db_file, Path(backup_dir) / f"before-update-{current}-{stamp}.db")
    log.info("Резервная копия перед обновлением схемы: %s", backup_file)
    try:
        upgrade(db_file)
    except Exception as exc:  # noqa: BLE001 — любая ошибка миграции ведёт к откату
        log.exception("Ошибка миграции, восстанавливаю базу из %s", backup_file)
        _restore_file(backup_file, db_file)
        raise MigrationError(
            f"Не удалось обновить базу данных ({exc}). База восстановлена в исходное состояние, "
            f"копия сохранена: {backup_file}"
        ) from exc
    return MigrationReport(created=False, from_revision=current, to_revision=current_revision(db_file), backup_file=backup_file)
