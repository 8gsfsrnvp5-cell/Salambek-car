"""Резервное копирование и восстановление.

Копия — zip-архив (или его зашифрованная версия) с базой, вложениями и
манифестом (версия программы, версия схемы, контрольные суммы SHA-256).
База копируется через SQLite backup API, а не копированием открытого файла.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from autoservice import __version__
from autoservice.application.context import AppContext, audit
from autoservice.application.settings_service import get_setting
from autoservice.backup import crypto
from autoservice.domain.errors import DomainError
from autoservice.persistence.migrate import known_revisions, prepare_database, sqlite_backup
from autoservice.persistence.models import BackupHistory, now
from autoservice.security.permissions import Perm

log = logging.getLogger(__name__)

FORMAT = "autoservice-backup/1"
DB_NAME = "autoservice.db"
MANIFEST = "manifest.json"
EXT_PLAIN = ".asbak"
EXT_ENC = ".asbak.enc"


class BackupError(DomainError):
    pass


@dataclass
class VerifyResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    manifest: dict | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) if Path(path).exists() else 0


def check_destination(dest_dir: Path, needed_bytes: int, min_free_mb: int = 200, create: bool = False) -> None:
    dest_dir = Path(dest_dir)
    if not dest_dir.exists():
        if create:
            dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise BackupError(f"Папка для копии недоступна: {dest_dir}. Проверьте, подключён ли внешний диск.")
    if not dest_dir.is_dir():
        raise BackupError(f"Путь не является папкой: {dest_dir}")
    probe = dest_dir / f".write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise BackupError(f"Нет доступа на запись в {dest_dir}: {exc}") from exc
    free = shutil.disk_usage(dest_dir).free
    reserve = min_free_mb * 1024 * 1024
    if free < needed_bytes + reserve:
        raise BackupError(
            f"Недостаточно места на диске {dest_dir.anchor or dest_dir}: свободно {free // (1024 * 1024)} МБ, "
            f"требуется не менее {(needed_bytes + reserve) // (1024 * 1024)} МБ"
        )


def _record(ctx: AppContext, **fields) -> int:
    with ctx.db.transaction() as s:
        row = BackupHistory(app_version=__version__, **fields)
        s.add(row)
        s.flush()
        return row.id


def _current_revision(db_file: Path) -> str:
    con = sqlite3.connect(db_file)
    try:
        row = con.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else ""
    finally:
        con.close()


def _rotate(dest_dir: Path, prefix: str, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted([p for p in dest_dir.glob(f"{prefix}*") if p.name.endswith((EXT_PLAIN, EXT_ENC))], key=lambda p: p.name, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
            log.info("Ротация копий: удалён %s", old)
        except OSError:
            log.warning("Не удалось удалить старую копию %s", old)


def create_backup(ctx: AppContext, dest_dir: Path, kind: str = "manual", password: str | None = None, keep: int | None = None,
                  create_dir: bool = False) -> dict:
    """Создаёт архив копии. Возвращает {path, size, sha256}."""
    ctx.require(Perm.BACKUP_RUN) if ctx.user else None
    paths = ctx.paths
    dest_dir = Path(dest_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = f"AutoServiceAI-{kind}-"
    final = dest_dir / f"{prefix}{stamp}{EXT_ENC if password else EXT_PLAIN}"
    try:
        with ctx.db.read() as s:
            min_free = int(get_setting(s, "backup.min_free_mb") or 200)
        needed = paths.db_file.stat().st_size + _dir_size(paths.attachments)
        check_destination(dest_dir, needed, min_free, create=create_dir)
        with tempfile.TemporaryDirectory(dir=paths.tmp) as tmp:
            tmp = Path(tmp)
            db_copy = sqlite_backup(paths.db_file, tmp / DB_NAME)
            con = sqlite3.connect(db_copy)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                con.close()
            if result != "ok":
                raise BackupError(f"Проверка целостности базы не пройдена: {result}")
            files = {DB_NAME: {"sha256": sha256_file(db_copy), "size": db_copy.stat().st_size}}
            zip_path = tmp / "backup.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
                z.write(db_copy, DB_NAME)
                if paths.attachments.exists():
                    for f in sorted(paths.attachments.rglob("*")):
                        if f.is_file():
                            rel = "attachments/" + f.relative_to(paths.attachments).as_posix()
                            z.write(f, rel)
                            files[rel] = {"sha256": sha256_file(f), "size": f.stat().st_size}
                manifest = {
                    "format": FORMAT,
                    "app_version": __version__,
                    "schema_revision": _current_revision(db_copy),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "kind": kind,
                    "computer": socket.gethostname(),
                    "platform": platform.platform(),
                    "encrypted": bool(password),
                    "files": files,
                }
                z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            part = final.with_name(final.name + ".part")
            if password:
                crypto.encrypt_file(zip_path, part, password)
            else:
                shutil.copyfile(zip_path, part)
            os.replace(part, final)
        digest = sha256_file(final)
        size = final.stat().st_size
        final.with_name(final.name + ".sha256").write_text(f"{digest}  {final.name}\n", encoding="ascii")
        _record(ctx, kind=kind, path=str(final), size_bytes=size, sha256=digest, encrypted=bool(password), status="ok",
                message="", schema_revision=manifest["schema_revision"])
        with ctx.db.transaction() as s:
            audit(s, ctx, "backup.create", "backup", None, path=str(final), kind=kind)
        if keep:
            _rotate(dest_dir, prefix, keep)
            for side in dest_dir.glob(f"{prefix}*.sha256"):
                if not side.with_name(side.name[: -len(".sha256")]).exists():
                    side.unlink(missing_ok=True)
        return {"path": final, "size": size, "sha256": digest}
    except Exception as exc:
        for leftover in (final, final.with_name(final.name + ".part")):
            if leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass
        msg = str(exc) if isinstance(exc, DomainError) else f"Ошибка резервного копирования: {exc}"
        log.exception("Резервная копия не создана")
        try:
            _record(ctx, kind=kind, path=str(final), status="failed", message=msg)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось записать историю копий")
        if isinstance(exc, BackupError):
            raise
        raise BackupError(msg) from exc


def _open_archive(path: Path, password: str | None, tmp: Path) -> Path:
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"Файл копии не найден: {path}")
    if crypto.is_encrypted(path):
        if not password:
            raise BackupError("Копия зашифрована — введите пароль")
        out = tmp / "decrypted.zip"
        crypto.decrypt_file(path, out, password)
        return out
    return path


def verify_backup(path: Path, password: str | None = None, work_dir: Path | None = None) -> VerifyResult:
    """Полная проверка: контрольная сумма файла, архив, суммы файлов, целостность базы, версия схемы."""
    path = Path(path)
    res = VerifyResult(ok=False)
    side = path.with_name(path.name + ".sha256")
    if side.exists():
        expected = side.read_text(encoding="ascii").split()[0]
        if sha256_file(path) != expected:
            res.messages.append("Контрольная сумма файла не совпадает — копия повреждена")
            return res
        res.messages.append("Контрольная сумма файла совпадает")
    try:
        with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
            tmp = Path(tmp)
            archive = _open_archive(path, password, tmp)
            with zipfile.ZipFile(archive) as z:
                bad = z.testzip()
                if bad:
                    res.messages.append(f"Повреждён файл в архиве: {bad}")
                    return res
                manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
                res.manifest = manifest
                if manifest.get("format") != FORMAT:
                    res.messages.append("Неизвестный формат копии")
                    return res
                for name, info in manifest["files"].items():
                    data_hash = hashlib.sha256()
                    with z.open(name) as fh:
                        for block in iter(lambda: fh.read(1024 * 1024), b""):
                            data_hash.update(block)
                    if data_hash.hexdigest() != info["sha256"]:
                        res.messages.append(f"Контрольная сумма не совпадает: {name}")
                        return res
                z.extract(DB_NAME, tmp)
            con = sqlite3.connect(tmp / DB_NAME)
            try:
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                revision = con.execute("select version_num from alembic_version").fetchone()[0]
            finally:
                con.close()
            if integrity != "ok":
                res.messages.append(f"База в копии повреждена: {integrity}")
                return res
            if revision not in known_revisions():
                res.messages.append("Копия создана более новой версией программы — обновите программу перед восстановлением")
                return res
            res.messages.append(f"Архив цел, файлов: {len(manifest['files'])}, база проверена, версия схемы {revision}")
            res.ok = True
            return res
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        res.messages.append(f"Архив повреждён или не является копией AutoService AI ({exc})")
        return res
    except DomainError as exc:
        res.messages.append(str(exc))
        return res


def record_verify(ctx: AppContext, path: Path, result: VerifyResult) -> None:
    with ctx.db.transaction() as s:
        row = s.execute(select(BackupHistory).where(BackupHistory.path == str(path)).order_by(BackupHistory.id.desc())).scalars().first()
        if row is not None:
            row.verified_at = now()
            row.verify_status = "ok" if result.ok else "failed"
        else:
            s.add(BackupHistory(kind="verify", path=str(path), status="ok" if result.ok else "failed",
                                message="; ".join(result.messages), app_version=__version__, verified_at=now(),
                                verify_status="ok" if result.ok else "failed"))


def restore_backup(ctx: AppContext, path: Path, password: str | None = None) -> dict:
    """Восстановление (только администратор). Текущая база сохраняется отдельной копией.

    После вызова прежний объект ctx.db закрыт — программа должна открыть базу заново.
    """
    ctx.require(Perm.BACKUP_RESTORE)
    paths = ctx.paths
    result = verify_backup(path, password, paths.tmp)
    if not result.ok:
        raise BackupError("Копия не прошла проверку: " + "; ".join(result.messages))
    safety = sqlite_backup(paths.db_file, paths.local_backups / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
    with ctx.db.transaction() as s:
        audit(s, ctx, "backup.restore_start", "backup", None, path=str(path), safety=str(safety))
    ctx.db.dispose()
    with tempfile.TemporaryDirectory(dir=paths.tmp) as tmp:
        tmp = Path(tmp)
        archive = _open_archive(Path(path), password, tmp)
        with zipfile.ZipFile(archive) as z:
            extract_dir = tmp / "x"
            for name in z.namelist():
                target = (extract_dir / name).resolve()
                if extract_dir.resolve() not in target.parents:
                    raise BackupError("Недопустимый путь внутри архива")
            z.extractall(extract_dir)
        for suffix in ("-wal", "-shm"):
            sidecar = paths.db_file.with_name(paths.db_file.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(extract_dir / DB_NAME, paths.db_file)
        src_att = extract_dir / "attachments"
        restored_files = 0
        if src_att.exists():
            for f in src_att.rglob("*"):
                if f.is_file():
                    dest = paths.attachments / f.relative_to(src_att)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    restored_files += 1
    migration = prepare_database(paths.db_file, paths.update_backups)
    return {"manifest": result.manifest, "safety_copy": safety, "attachments": restored_files,
            "migrated": migration.upgraded, "schema": migration.to_revision}


def list_history(ctx: AppContext, limit: int = 200) -> list[dict]:
    with ctx.db.read() as s:
        return [
            {"id": r.id, "ts": r.ts, "kind": r.kind, "path": r.path, "size_bytes": r.size_bytes, "sha256": r.sha256,
             "encrypted": r.encrypted, "status": r.status, "message": r.message, "verified_at": r.verified_at,
             "verify_status": r.verify_status, "exists": Path(r.path).exists() if r.path else False}
            for r in s.execute(select(BackupHistory).order_by(BackupHistory.id.desc()).limit(limit)).scalars()
        ]


def latest_backup_file(ctx: AppContext) -> Path | None:
    with ctx.db.read() as s:
        for r in s.execute(select(BackupHistory).where(BackupHistory.status == "ok", BackupHistory.kind != "verify")
                           .order_by(BackupHistory.id.desc())).scalars():
            if r.path and Path(r.path).exists():
                return Path(r.path)
    return None
