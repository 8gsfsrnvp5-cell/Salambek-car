"""Права ролей. Проверяются в сервисном слое, а не только в интерфейсе."""

from __future__ import annotations

from autoservice.domain.enums import Role


class Perm:
    CATALOG_EDIT = "catalog.edit"
    STOCK_OPS = "stock.ops"
    PURCHASE_EDIT = "purchase.edit"
    PURCHASE_POST = "purchase.post"
    SALE_EDIT = "sale.edit"
    SALE_POST = "sale.post"
    SALE_RETURN = "sale.return"
    CUSTOMER_EDIT = "customer.edit"
    WORKORDER_EDIT = "workorder.edit"
    PAYMENT_CREATE = "payment.create"
    LIBRARY_EDIT = "library.edit"
    REPORTS_OPERATIONAL = "reports.operational"
    REPORTS_FINANCE = "reports.finance"
    FINANCE_VIEW = "finance.view"
    EXPENSE_EDIT = "expense.edit"
    OPERATION_CANCEL = "operation.cancel"
    MARKUP_DEFAULT = "markup.default"
    SETTINGS_EDIT = "settings.edit"
    USERS_MANAGE = "users.manage"
    AUDIT_VIEW = "audit.view"
    BACKUP_RUN = "backup.run"
    BACKUP_CONFIGURE = "backup.configure"
    BACKUP_RESTORE = "backup.restore"
    ASSISTANT_USE = "assistant.use"
    IMPORT_INVOICES = "import.invoices"
    EMPLOYEES_EDIT = "employees.edit"


MANAGER_PERMS = {
    Perm.CATALOG_EDIT,
    Perm.STOCK_OPS,
    Perm.PURCHASE_EDIT,
    Perm.PURCHASE_POST,
    Perm.SALE_EDIT,
    Perm.SALE_POST,
    Perm.SALE_RETURN,
    Perm.CUSTOMER_EDIT,
    Perm.WORKORDER_EDIT,
    Perm.PAYMENT_CREATE,
    Perm.LIBRARY_EDIT,
    Perm.REPORTS_OPERATIONAL,
    Perm.BACKUP_RUN,
    Perm.ASSISTANT_USE,
    Perm.IMPORT_INVOICES,
    Perm.EMPLOYEES_EDIT,
}

ALL_PERMS = {v for k, v in vars(Perm).items() if not k.startswith("_")}

ROLE_PERMS = {Role.ADMIN: ALL_PERMS, Role.MANAGER: MANAGER_PERMS}

PERM_TITLES = {
    Perm.REPORTS_FINANCE: "финансовые отчёты",
    Perm.FINANCE_VIEW: "просмотр финансов",
    Perm.EXPENSE_EDIT: "учёт расходов",
    Perm.OPERATION_CANCEL: "отмена проведённых операций",
    Perm.MARKUP_DEFAULT: "изменение стандартной наценки",
    Perm.SETTINGS_EDIT: "изменение настроек",
    Perm.USERS_MANAGE: "управление пользователями",
    Perm.AUDIT_VIEW: "журнал действий",
    Perm.BACKUP_CONFIGURE: "настройка резервного копирования",
    Perm.BACKUP_RESTORE: "восстановление из резервной копии",
}


def has_perm(role: str | None, perm: str) -> bool:
    return bool(role) and perm in ROLE_PERMS.get(role, set())
