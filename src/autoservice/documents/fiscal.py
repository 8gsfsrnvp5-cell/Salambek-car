"""Интерфейс будущей интеграции с ККТ (онлайн-кассой).

В версии 1.0 фискализация НЕ реализована: программа печатает только
нефискальные документы с пометкой «Не является кассовым чеком». Для
фискальных чеков нужна сертифицированная ККТ и её драйвер; реализация
подключается через этот интерфейс отдельно.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass
class FiscalLine:
    name: str
    qty: Decimal
    price: Decimal
    amount: Decimal
    vat_code: str  # задаётся настройками ККТ, не программой
    item_type: str  # «товар» / «услуга»


@dataclass
class FiscalReceipt:
    lines: list[FiscalLine]
    payment_method: str
    total: Decimal
    customer_contact: str = ""


class FiscalRegistrar(Protocol):
    def is_available(self) -> bool: ...

    def status_text(self) -> str: ...

    def register(self, receipt: FiscalReceipt) -> str: ...


class NotConfiguredRegistrar:
    def is_available(self) -> bool:
        return False

    def status_text(self) -> str:
        return "ККТ не подключена. Печатаются только нефискальные документы."

    def register(self, receipt: FiscalReceipt) -> str:
        raise RuntimeError("Фискализация не настроена")


def current_registrar() -> FiscalRegistrar:
    return NotConfiguredRegistrar()
