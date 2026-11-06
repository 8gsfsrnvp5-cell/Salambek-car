"""Расположение пользовательских данных и ресурсов программы.

Рабочие данные хранятся отдельно от файлов программы, чтобы обновление или
удаление приложения не затрагивало базу:

1. переменная окружения ``AUTOSERVICE_DATA_DIR`` (тесты, переносная установка);
2. файл ``autoservice.ini`` рядом с исполняемым файлом (пишет установщик);
3. ``%ProgramData%\\AutoServiceAI`` — общий каталог Windows.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from autoservice import APP_ID

ENV_DATA_DIR = "AUTOSERVICE_DATA_DIR"
INI_NAME = "autoservice.ini"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def package_dir() -> Path:
    """Каталог пакета autoservice (в собранной программе — внутри _internal)."""
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return package_dir().joinpath(*parts)


def _default_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def resolve_data_root() -> Path:
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser().resolve()
    ini = executable_dir() / INI_NAME
    if ini.is_file():
        cfg = configparser.ConfigParser()
        try:
            cfg.read(ini, encoding="utf-8")
            value = cfg.get("paths", "data_dir", fallback="").strip()
        except (configparser.Error, OSError, UnicodeDecodeError):
            value = ""
        if value:
            return Path(os.path.expandvars(value)).expanduser().resolve()
    return _default_root()


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def db_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_file(self) -> Path:
        return self.db_dir / "autoservice.db"

    @property
    def attachments(self) -> Path:
        return self.root / "attachments"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def local_backups(self) -> Path:
        return self.backups / "local"

    @property
    def update_backups(self) -> Path:
        return self.backups / "before-update"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    def ensure(self) -> "AppPaths":
        for p in (
            self.root,
            self.db_dir,
            self.attachments,
            self.local_backups,
            self.update_backups,
            self.logs,
            self.exports,
            self.tmp,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(resolve_data_root())
