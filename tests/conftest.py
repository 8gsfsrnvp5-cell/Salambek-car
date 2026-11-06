import os
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from autoservice.application import auth, bootstrap  # noqa: E402
from autoservice.config.paths import AppPaths  # noqa: E402
from autoservice.domain.enums import Role  # noqa: E402


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data-root"


@pytest.fixture
def app(data_dir):
    """Чистая установка: каталоги, база, миграции, администратор."""
    result = bootstrap.start(AppPaths(data_dir), with_logging=False)
    admin = auth.create_first_admin(result.ctx, "admin", "Admin-pass-1", "Администратор")
    ctx = result.ctx.as_user(admin)
    yield ctx
    ctx.db.dispose()


@pytest.fixture
def manager(app):
    uid = auth.create_user(app, "manager", "Manager-pass-1", Role.MANAGER, "Менеджер")
    return app.as_user(auth.login(app, "manager", "Manager-pass-1"))


@pytest.fixture
def setup_markup(app):
    from autoservice.application.settings_service import update_settings

    update_settings(app, {"pricing.default_markup": "30"})
    return app


def D(x) -> Decimal:
    return Decimal(str(x))


@pytest.fixture
def make_part(app):
    from autoservice.application import catalog

    def _make(name="Масляный фильтр", oem="90915-YZZE1", unit="шт", markup=None, category="filters", **extra):
        with app.db.read() as s:
            cat = catalog.category_by_code(s, category)
        return catalog.create_part(app, {"name": name, "oem_number": oem, "unit": unit, "brand": "Toyota",
                                         "category_id": cat.id if cat else None, "markup_pct": markup, **extra})

    return _make


@pytest.fixture
def supplier(app):
    from autoservice.application import catalog

    return catalog.save_supplier(app, {"name": "ООО «Тестовый поставщик»", "inn": "7701234567", "lead_time_days": 3})


@pytest.fixture
def purchase(app, supplier):
    """Создаёт и проводит закупку: purchase([(part_id, qty, price), ...])."""
    from autoservice.application import purchasing

    counter = {"n": 0}

    def _purchase(lines, extra="0", markup=None):
        counter["n"] += 1
        pid = purchasing.create_purchase(app, supplier, f"ТН-{counter['n']}", extra_costs=extra)
        for part_id, q, price in lines:
            purchasing.add_purchase_line(app, pid, part_id, q, price, markup_pct=markup)
        purchasing.post_purchase(app, pid)
        return pid

    return _purchase
