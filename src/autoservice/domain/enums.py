"""Статусы и справочные значения с русскими подписями."""

from __future__ import annotations


class DocStatus:
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"

    TITLES = {DRAFT: "Черновик", POSTED: "Проведён", CANCELLED: "Отменён"}


class WOStatus:
    NEW = "new"
    DIAGNOSTICS = "diagnostics"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PARTS = "awaiting_parts"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    ISSUED = "issued"
    CANCELLED = "cancelled"

    TITLES = {
        NEW: "Новый",
        DIAGNOSTICS: "Диагностика",
        AWAITING_APPROVAL: "Ожидает согласования",
        AWAITING_PARTS: "Ожидает запчасти",
        IN_PROGRESS: "В ремонте",
        READY: "Готов",
        ISSUED: "Выдан",
        CANCELLED: "Отменён",
    }
    ORDER = [NEW, DIAGNOSTICS, AWAITING_APPROVAL, AWAITING_PARTS, IN_PROGRESS, READY, ISSUED, CANCELLED]
    OPEN = {NEW, DIAGNOSTICS, AWAITING_APPROVAL, AWAITING_PARTS, IN_PROGRESS, READY}
    IN_SHOP = {NEW, DIAGNOSTICS, AWAITING_APPROVAL, AWAITING_PARTS, IN_PROGRESS, READY}

    TRANSITIONS = {
        NEW: {DIAGNOSTICS, AWAITING_APPROVAL, AWAITING_PARTS, IN_PROGRESS, CANCELLED},
        DIAGNOSTICS: {AWAITING_APPROVAL, AWAITING_PARTS, IN_PROGRESS, CANCELLED},
        AWAITING_APPROVAL: {DIAGNOSTICS, AWAITING_PARTS, IN_PROGRESS, CANCELLED},
        AWAITING_PARTS: {AWAITING_APPROVAL, IN_PROGRESS, CANCELLED},
        IN_PROGRESS: {AWAITING_APPROVAL, AWAITING_PARTS, READY, CANCELLED},
        READY: {IN_PROGRESS, ISSUED},
        ISSUED: set(),
        CANCELLED: set(),
    }


class PartLineStatus:
    PLANNED = "planned"
    RESERVED = "reserved"
    ISSUED = "issued"
    RETURNED = "returned"

    TITLES = {PLANNED: "Запланирована", RESERVED: "В резерве", ISSUED: "Выдана со склада", RETURNED: "Возвращена на склад"}


class Verification:
    CONFIRMED = "confirmed"
    NEEDS_CHECK = "needs_check"

    TITLES = {CONFIRMED: "Подтверждено", NEEDS_CHECK: "Требует проверки"}


class Confidence:
    CONFIRMED = "confirmed"
    NEEDS_CHECK = "needs_check"
    INSUFFICIENT = "insufficient"

    TITLES = {CONFIRMED: "Подтверждено", NEEDS_CHECK: "Требует проверки", INSUFFICIENT: "Недостаточно данных"}


class PaymentMethod:
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"

    TITLES = {CASH: "Наличные", CARD: "Банковская карта", TRANSFER: "Безналичный перевод"}


class PaymentKind:
    CUSTOMER_PAYMENT = "customer_payment"
    CUSTOMER_REFUND = "customer_refund"
    SUPPLIER_PAYMENT = "supplier_payment"
    SUPPLIER_REFUND = "supplier_refund"

    TITLES = {
        CUSTOMER_PAYMENT: "Оплата от клиента",
        CUSTOMER_REFUND: "Возврат денег клиенту",
        SUPPLIER_PAYMENT: "Оплата поставщику",
        SUPPLIER_REFUND: "Возврат денег от поставщика",
    }
    DIRECTION = {
        CUSTOMER_PAYMENT: "in",
        CUSTOMER_REFUND: "out",
        SUPPLIER_PAYMENT: "out",
        SUPPLIER_REFUND: "in",
    }


class MovementKind:
    PURCHASE_IN = "purchase_in"
    PURCHASE_CANCEL_OUT = "purchase_cancel_out"
    SUPPLIER_RETURN_OUT = "supplier_return_out"
    SALE_OUT = "sale_out"
    SALE_CANCEL_IN = "sale_cancel_in"
    SALE_RETURN_IN = "sale_return_in"
    WO_ISSUE = "wo_issue"
    WO_RETURN_IN = "wo_return_in"
    ADJUST_IN = "adjust_in"
    ADJUST_OUT = "adjust_out"
    OPENING_IN = "opening_in"
    INVENTORY_IN = "inventory_in"
    INVENTORY_OUT = "inventory_out"

    TITLES = {
        PURCHASE_IN: "Поступление по закупке",
        PURCHASE_CANCEL_OUT: "Отмена закупки",
        SUPPLIER_RETURN_OUT: "Возврат поставщику",
        SALE_OUT: "Продажа",
        SALE_CANCEL_IN: "Отмена продажи",
        SALE_RETURN_IN: "Возврат от покупателя",
        WO_ISSUE: "Выдача в ремонт",
        WO_RETURN_IN: "Возврат из ремонта на склад",
        ADJUST_IN: "Оприходование",
        ADJUST_OUT: "Списание",
        OPENING_IN: "Начальный остаток",
        INVENTORY_IN: "Излишек по инвентаризации",
        INVENTORY_OUT: "Недостача по инвентаризации",
    }
    CONSUMPTION = {SALE_OUT, WO_ISSUE}


class Role:
    ADMIN = "admin"
    MANAGER = "manager"

    TITLES = {ADMIN: "Администратор", MANAGER: "Менеджер"}


UNITS = ["шт", "л", "кг", "м", "компл", "пара", "уп"]

FUEL_TYPES = ["Бензин", "Дизель", "Газ/бензин", "Гибрид", "Электро", "Другое"]
TRANSMISSIONS = ["Механическая", "Автоматическая", "Вариатор", "Робот", "Другое"]
DRIVES = ["Передний", "Задний", "Полный", "Другое"]
ENGINE_TYPES = ["ДВС", "Гибрид", "Электродвигатель", "Другое"]

EXPENSE_CATEGORIES = [
    "Аренда",
    "Зарплата",
    "Коммунальные услуги",
    "Инструмент и оборудование",
    "Хозяйственные расходы",
    "Налоги и сборы",
    "Реклама",
    "Транспорт и доставка",
    "Связь и интернет",
    "Прочее",
]

CRITICAL_SYSTEM_CODES = {"brakes", "steering", "electrical", "electronics", "suspension", "fuel"}
