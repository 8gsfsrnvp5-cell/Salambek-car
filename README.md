# AutoService AI 1.0

Локальная программа для Windows для управления мультибрендовым автосервисом: склад запчастей, закупки, продажи,
наценка 0–100 %, автомобили и клиенты, заказ-наряды, оплаты и долги, документы PDF и печать, отчёты Excel,
библиотека автодела, офлайн-помощник по неисправностям, распознавание накладных, резервное копирование.

Работает на одном компьютере **без интернета**, без облачных сервисов, подписок и телеметрии. Данные хранятся
локально в SQLite (`C:\ProgramData\AutoServiceAI`), отдельно от файлов программы.

## Установка

Готовый установщик `AutoServiceAI-Setup.exe` собирается в GitHub Actions (вкладка **Actions** → последний успешный
запуск «Тесты и сборка установщика Windows» → артефакт **AutoServiceAI-Setup**) или локально скриптом
`scripts\build.ps1`. Порядок установки, обновления, удаления и переноса — [docs/INSTALL.md](docs/INSTALL.md).

## Документация

| Документ | Содержание |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Руководство менеджера |
| [docs/BACKUP.md](docs/BACKUP.md) | Резервное копирование и восстановление |
| [docs/INSTALL.md](docs/INSTALL.md) | Установка Windows, обновление, перенос на другой компьютер |
| [docs/CALCULATIONS.md](docs/CALCULATIONS.md) | Правила расчётов: деньги, наценка, FIFO, округление, выручка |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура, схема данных, безопасность, ограничения |
| [docs/AI.md](docs/AI.md) | ИИ-помощник, локальная модель, распознавание накладных, рекомендации |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Запуск для разработчика, тесты, миграции |
| [docs/BUILD.md](docs/BUILD.md) | Сборка установщика, CI, подпись кода |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | План тестирования, соответствие 33 обязательным сценариям |
| [CHANGELOG.md](CHANGELOG.md) | Журнал изменений |

## Технологии

Python 3.12 · PySide6 (Qt 6) · SQLite + SQLAlchemy 2 · Alembic · openpyxl · ReportLab · pypdf · cryptography ·
PyInstaller · Inno Setup · pytest.

## Структура

```
src/autoservice/   исходный код (domain, persistence, application, documents, knowledge, ai, backup, security, ui)
tests/             автотесты (pytest)
installer/         PyInstaller spec и сценарий Inno Setup
scripts/           сборка установщика, генерация значка
docs/              документация
.github/workflows/ тесты, сборка и проверка установщика в GitHub Actions
```

## Быстрый старт разработчика

```powershell
py -V:3.12 -m venv .venv
$env:DISABLE_SQLALCHEMY_CEXT = "1"
.venv\Scripts\python -m pip install --no-binary SQLAlchemy -r requirements-dev.txt
.venv\Scripts\python -m pip install -e . --no-deps
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python -m pytest -q
Remove-Item Env:\QT_QPA_PLATFORM; $env:AUTOSERVICE_DATA_DIR = "$PWD\data-dev"; .venv\Scripts\python -m autoservice
```

В репозиторий не попадают базы данных, копии, вложения, пароли и клиентские данные.
