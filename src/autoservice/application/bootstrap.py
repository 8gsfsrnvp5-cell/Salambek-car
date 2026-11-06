"""Запуск: каталоги данных, база, миграции, начальные справочники."""

from __future__ import annotations

import logging
import logging.handlers
from dataclasses import dataclass

from autoservice import __version__
from autoservice.application.context import AppContext
from autoservice.config.paths import AppPaths
from autoservice.persistence.db import Database
from autoservice.persistence.migrate import MigrationReport, prepare_database

log = logging.getLogger(__name__)


@dataclass
class StartupResult:
    ctx: AppContext
    migration: MigrationReport


def setup_logging(paths: AppPaths) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_autoservice", False) for h in root.handlers):
        return
    handler = logging.handlers.RotatingFileHandler(paths.logs / "autoservice.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._autoservice = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def seed_reference_data(ctx: AppContext) -> None:
    from autoservice.application.catalog import seed_categories
    from autoservice.knowledge.rules import seed_builtin_rules

    with ctx.db.transaction() as s:
        seed_categories(s)
    with ctx.db.transaction() as s:
        seed_builtin_rules(s)


def start(paths: AppPaths | None = None, with_logging: bool = True) -> StartupResult:
    paths = (paths or AppPaths.default()).ensure()
    if with_logging:
        setup_logging(paths)
    log.info("AutoService AI %s, данные: %s", __version__, paths.root)
    report = prepare_database(paths.db_file, paths.update_backups)
    db = Database(paths.db_file)
    ctx = AppContext(db=db, paths=paths)
    seed_reference_data(ctx)
    return StartupResult(ctx=ctx, migration=report)
