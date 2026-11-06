"""Окружение Alembic. Используется и программой при запуске, и разработчиком."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from autoservice.persistence.models import Base

config = context.config
target_metadata = Base.metadata

FTS_TABLE_PREFIX = "knowledge_fts"


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name and name.startswith(FTS_TABLE_PREFIX):
        return False
    return True


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # Завершаем неявную транзакцию, иначе Alembic сочтёт её внешней и не зафиксирует миграции.
        conn.commit()
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Офлайн-режим генерации SQL не используется")
run_migrations_online()
