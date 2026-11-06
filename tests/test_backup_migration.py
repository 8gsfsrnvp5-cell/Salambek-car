"""Сценарии 28–33: резервное копирование, восстановление, перенос, офлайн, перезапуск, обновление."""

import shutil
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from autoservice.application import auth, bootstrap, catalog, sales
from autoservice.backup import crypto, scheduler
from autoservice.backup.service import BackupError, create_backup, restore_backup, verify_backup
from autoservice.config.paths import AppPaths
from autoservice.domain.errors import PermissionDenied
from autoservice.persistence import migrate
from autoservice.persistence.migrate import MigrationError, prepare_database

from helpers import populate


def test_28_backup_and_verify(app, tmp_path):
    populate(app)
    dest = tmp_path / "external"
    dest.mkdir()
    info = create_backup(app, dest, kind="manual")
    assert info["path"].exists() and info["path"].with_name(info["path"].name + ".sha256").exists()
    res = verify_backup(info["path"])
    assert res.ok, res.messages
    assert res.manifest["schema_revision"] == migrate.head_revision()


def test_encrypted_backup_requires_password(app, tmp_path):
    populate(app)
    info = create_backup(app, tmp_path, password="Очень-секретно-1")
    assert crypto.is_encrypted(info["path"])
    assert not verify_backup(info["path"]).ok
    assert not verify_backup(info["path"], "неверный-пароль").ok
    assert verify_backup(info["path"], "Очень-секретно-1").ok


def test_29_30_restore_on_another_computer(app, tmp_path):
    data = populate(app)
    archive = create_backup(app, tmp_path / "usb", kind="manual", create_dir=True)["path"]
    # «Другой компьютер»: чистая установка в другом каталоге данных.
    other = bootstrap.start(AppPaths(tmp_path / "other-pc"), with_logging=False)
    admin2 = auth.create_first_admin(other.ctx, "boss", "Boss-pass-123")
    ctx2 = other.ctx.as_user(admin2)
    restore_backup(ctx2, archive)
    fresh = bootstrap.start(AppPaths(tmp_path / "other-pc"), with_logging=False).ctx
    user = auth.login(fresh, "admin", "Admin-pass-1")  # пользователи пришли из копии
    ctx3 = fresh.as_user(user)
    assert sales.get_sale(ctx3, data["sale"])["total_net"] == sales.get_sale(app, data["sale"])["total_net"]
    assert catalog.get_part(ctx3, data["oil"])["on_hand"] == catalog.get_part(app, data["oil"])["on_hand"]
    assert any((tmp_path / "other-pc" / "backups" / "local").glob("before-restore-*.db"))


def test_restore_requires_admin(manager, app, tmp_path):
    archive = create_backup(app, tmp_path)["path"]
    with pytest.raises(PermissionDenied):
        restore_backup(manager, archive)


def test_corrupted_archive_rejected(app, tmp_path):
    populate(app)
    path = create_backup(app, tmp_path)["path"]
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert not verify_backup(path).ok
    path.with_name(path.name + ".sha256").unlink()
    assert not verify_backup(path).ok
    with pytest.raises(BackupError):
        restore_backup(app, path)
    (tmp_path / "junk.asbak").write_bytes(b"not a zip at all")
    assert not verify_backup(tmp_path / "junk.asbak").ok


def test_disk_unavailable_and_no_space(app, tmp_path, monkeypatch):
    with pytest.raises(BackupError, match="недоступна"):
        create_backup(app, tmp_path / "missing-drive")
    import autoservice.backup.service as svc

    monkeypatch.setattr(svc.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 10, 1024))
    with pytest.raises(BackupError, match="Недостаточно места"):
        create_backup(app, tmp_path)
    from autoservice.backup.service import list_history

    assert [h["status"] for h in list_history(app)][:2] == ["failed", "failed"]


def test_scheduler_runs_due_tasks(app, tmp_path):
    from autoservice.application.settings_service import update_settings

    ext = tmp_path / "ext"
    ext.mkdir()
    update_settings(app, {"backup.external_path": str(ext)})
    r1 = scheduler.tick(app, datetime(2026, 11, 6, 10, 0))
    assert any("Локальный снимок" in x for x in r1.done) and any("внешний диск" in x for x in r1.done)
    assert any("Проверка копии пройдена" in x for x in r1.done)
    r2 = scheduler.tick(app, datetime(2026, 11, 6, 11, 0))
    assert r2.done == []  # ещё не прошло 2 часа, внешняя копия уже сегодня
    r3 = scheduler.tick(app, datetime(2026, 11, 7, 12, 30))
    assert len(r3.done) == 2
    shutil.rmtree(ext)
    r4 = scheduler.tick(app, datetime(2026, 11, 8, 15, 0))
    assert any("внешний диск не создана" in w for w in r4.warnings)
    r5 = scheduler.tick(app, datetime(2026, 12, 10, 15, 0))
    assert r5.reminders


def test_rotation_keeps_limit(app, tmp_path):
    for i in range(4):
        create_backup(app, tmp_path, kind="auto", keep=2)
        import time

        time.sleep(1.05)
    assert len(list(tmp_path.glob("AutoServiceAI-auto-*.asbak"))) == 2


def test_31_works_without_network(data_dir, monkeypatch, tmp_path):
    """Все основные операции выполняются при запрете любых сетевых соединений."""
    def blocked(*a, **k):
        raise AssertionError("Попытка сетевого соединения!")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    res = bootstrap.start(AppPaths(data_dir), with_logging=False)
    admin = auth.create_first_admin(res.ctx, "admin", "Admin-pass-1")
    ctx = res.ctx.as_user(admin)
    populate(ctx)
    from autoservice.ai.assistant import DiagnosisInput, analyze
    from autoservice.documents import generator

    generator.work_order_pdf(ctx, 1)
    create_backup(ctx, tmp_path)
    analyze(ctx, DiagnosisInput(make="Toyota", model="Camry", complaint="горит P0171"))


def test_32_data_survives_restart(data_dir):
    res = bootstrap.start(AppPaths(data_dir), with_logging=False)
    admin = auth.create_first_admin(res.ctx, "admin", "Admin-pass-1")
    data = populate(res.ctx.as_user(admin))
    res.ctx.db.dispose()
    again = bootstrap.start(AppPaths(data_dir), with_logging=False)
    assert not again.migration.created and not again.migration.upgraded
    ctx = again.ctx.as_user(auth.login(again.ctx, "admin", "Admin-pass-1"))
    assert sales.get_sale(ctx, data["sale"])["status"] == "posted"
    assert again.ctx.db.integrity_check() == []


def test_33_update_existing_database_keeps_data(tmp_path):
    """База версии схемы 0001 с данными → обновление до текущей → данные на месте, копия создана."""
    db = tmp_path / "data" / "autoservice.db"
    db.parent.mkdir(parents=True)
    migrate.upgrade(db, "0001")
    con = sqlite3.connect(db)
    con.execute("insert into customers(kind,name,name_search,phone,phone_normalized,email,inn,address,notes,created_at,updated_at)"
                " values('person','Старый клиент','старый клиент','','','','','','',datetime('now'),datetime('now'))")
    con.execute("insert into knowledge_sources(title,kind,source,make,model,engine,verification_status,content_text,notes,created_at)"
                " values('Регламент ТО','regulation','мастер','Toyota','','','needs_check','замена масла каждые 10000 км','',datetime('now'))")
    con.commit()
    con.close()
    report = prepare_database(db, tmp_path / "backups")
    assert report.from_revision == "0001" and report.to_revision == migrate.head_revision()
    assert report.backup_file and report.backup_file.exists()
    con = sqlite3.connect(db)
    assert con.execute("select name from customers").fetchone()[0] == "Старый клиент"
    # данные, созданные до обновления, попали в новый полнотекстовый индекс
    assert con.execute("select count(*) from knowledge_fts where knowledge_fts match 'масла'").fetchone()[0] == 1
    con.close()


def test_failed_update_rolls_back(tmp_path, monkeypatch):
    db = tmp_path / "autoservice.db"
    migrate.upgrade(db, "0001")
    con = sqlite3.connect(db)
    con.execute("insert into sequences(name,next_value) values('marker', 42)")
    con.commit()
    con.close()

    def broken(db_file, revision="head"):
        c = sqlite3.connect(db_file)
        c.execute("delete from sequences")
        c.commit()
        c.close()
        raise RuntimeError("сбой миграции")

    monkeypatch.setattr(migrate, "upgrade", broken)
    with pytest.raises(MigrationError, match="восстановлена"):
        prepare_database(db, tmp_path / "bk")
    con = sqlite3.connect(db)
    assert con.execute("select next_value from sequences where name='marker'").fetchone()[0] == 42
    con.close()


def test_newer_database_refused(tmp_path):
    db = tmp_path / "autoservice.db"
    migrate.upgrade(db)
    con = sqlite3.connect(db)
    con.execute("update alembic_version set version_num='9999'")
    con.commit()
    con.close()
    with pytest.raises(MigrationError, match="более новой версией"):
        prepare_database(db, tmp_path)
