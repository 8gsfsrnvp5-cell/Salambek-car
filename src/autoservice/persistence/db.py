"""Подключение к локальной SQLite."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _py_lower(value):
    return value.lower() if isinstance(value, str) else value


def _py_upper(value):
    return value.upper() if isinstance(value, str) else value


def make_engine(db_file: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        # Управляем транзакциями сами (рекомендация SQLAlchemy для pysqlite).
        dbapi_connection.isolation_level = None
        # Встроенные lower/upper SQLite понимают только латиницу — заменяем на
        # юникодные, чтобы поиск без учёта регистра работал для кириллицы.
        dbapi_connection.create_function("lower", 1, _py_lower, deterministic=True)
        dbapi_connection.create_function("upper", 1, _py_upper, deterministic=True)
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


class Database:
    def __init__(self, db_file: Path):
        self.db_file = Path(db_file)
        self.engine = make_engine(self.db_file)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False, future=True)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Атомарная операция: либо всё сохраняется, либо ничего."""
        session = self._sessions()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    @contextmanager
    def read(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    def integrity_check(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql("PRAGMA integrity_check").fetchall()
            fk = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        problems = [r[0] for r in rows if r[0] != "ok"]
        problems += [f"Нарушение внешнего ключа: {tuple(r)}" for r in fk]
        return problems

    def dispose(self) -> None:
        self.engine.dispose()
