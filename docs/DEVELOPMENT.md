# Инструкция для разработчика

## Окружение

Нужны Windows 10/11 x64, Python 3.12, Git.

```powershell
py -V:3.12 -m venv .venv            # или: py -3.12 -m venv .venv
$env:DISABLE_SQLALCHEMY_CEXT = "1"
.venv\Scripts\python -m pip install --no-binary SQLAlchemy -r requirements-dev.txt
.venv\Scripts\python -m pip install -e . --no-deps
```

SQLAlchemy ставится без C-ускорителей: на компьютерах с включённым «Интеллектуальным контролем приложений»
(Smart App Control) неподписанные `.pyd` блокируются Windows. Функционально чистый Python-режим идентичен.

## Запуск

```powershell
$env:AUTOSERVICE_DATA_DIR = "$PWD\data-dev"   # отдельная папка данных для разработки
.venv\Scripts\python -m autoservice
```

Самопроверка: `.venv\Scripts\python -m autoservice --self-test`.

## Тесты

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python -m pytest -q
```

## Новая миграция базы

1. Измените модели в `src/autoservice/persistence/models.py`.
2. Сгенерируйте сценарий и проверьте его вручную:
   ```powershell
   .venv\Scripts\python -c "from pathlib import Path; from alembic import command; from autoservice.persistence.migrate import alembic_config; command.revision(alembic_config(Path('tmp.db')), message='описание', autogenerate=True, rev_id='0003')"
   ```
   `tmp.db` должна быть обновлена до последней версии (`autoservice.persistence.migrate.upgrade`).
3. Добавьте тест обновления базы с данными (см. `test_33_update_existing_database_keeps_data`).

## Правила кода

* Денежные значения — только `Decimal`; округление — функции `domain/money.py`.
* Любая операция, меняющая данные, — функция в `application/`, внутри `ctx.db.transaction()`, с `ctx.require(Perm...)`
  и записью `audit(...)`.
* Сообщения пользователю — на русском, через `DomainError` и его подклассы.
* В репозиторий не попадают базы, копии, вложения, пароли и клиентские данные (см. `.gitignore`).
