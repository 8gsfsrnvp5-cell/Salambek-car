"""Настройки программы. Ничего не придумываем: реквизиты и налоги пусты, пока их не заполнят."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from autoservice.application.context import AppContext, audit
from autoservice.domain.markup import validate_markup
from autoservice.domain.money import D, non_negative_money
from autoservice.persistence.models import Setting
from autoservice.security.permissions import Perm

DEFAULTS: dict[str, object] = {
    # реквизиты автосервиса (печатаются только заполненные)
    "company.name": "",
    "company.legal_name": "",
    "company.inn": "",
    "company.kpp": "",
    "company.ogrn": "",
    "company.address": "",
    "company.phone": "",
    "company.email": "",
    "company.bank_details": "",
    "company.logo_path": "",
    "company.document_footer": "",
    # цены
    "pricing.default_markup": None,  # не задана, пока менеджер не выберет
    "pricing.labor_rate": None,  # стоимость нормо-часа, если используется
    # налоги — не настроены, налоги не рассчитываются
    "tax.regime": "",
    "tax.vat_mode": "not_configured",  # not_configured | not_payer | included | on_top
    "tax.vat_rate": None,
    # склад
    "stock.allow_negative": False,
    # резервное копирование
    "backup.local_interval_hours": 2,
    "backup.local_keep": 24,
    "backup.external_path": "",
    "backup.external_keep": 30,
    "backup.external_daily": True,
    "backup.verify_weekly": True,
    "backup.monthly_reminder": True,
    "backup.encrypt": False,
    "backup.min_free_mb": 200,
    "backup.last_local": None,
    "backup.last_external": None,
    "backup.last_verify": None,
    "backup.last_monthly_reminder": None,
    # локальная языковая модель (режим B)
    "llm.enabled": False,
    "llm.kind": "ollama",
    "llm.url": "http://127.0.0.1:11434",
    "llm.model": "",
    # распознавание
    "ocr.tesseract_path": "",
    "ocr.languages": "rus+eng",
    # ИИ-помощник
    "assistant.use_builtin_rules": True,
    # печать
    "print.receipt_width_mm": 80,
}

VAT_MODES = {
    "not_configured": "Не настроено (НДС не указывается)",
    "not_payer": "Без НДС (не плательщик)",
    "included": "НДС включён в цену",
    "on_top": "НДС сверху",
}

TAX_REGIMES = ["", "ОСНО", "УСН «Доходы»", "УСН «Доходы минус расходы»", "АУСН", "ПСН", "НПД", "Другое"]


def get_setting(s: Session, key: str):
    row = s.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def get_all(s: Session) -> dict:
    values = dict(DEFAULTS)
    for row in s.query(Setting).all():
        values[row.key] = row.value
    return values


def _store(s: Session, key: str, value) -> None:
    row = s.get(Setting, key)
    if isinstance(value, Decimal):
        value = str(value)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


def set_system_value(ctx: AppContext, key: str, value) -> None:
    """Служебные отметки (время последней копии и т. п.) — без проверки прав."""
    with ctx.db.transaction() as s:
        _store(s, key, value)


def update_settings(ctx: AppContext, values: dict) -> None:
    if "pricing.default_markup" in values:
        ctx.require(Perm.MARKUP_DEFAULT)
    if any(k.startswith("backup.") for k in values):
        ctx.require(Perm.BACKUP_CONFIGURE)
    if any(not (k.startswith("backup.") or k == "pricing.default_markup") for k in values):
        ctx.require(Perm.SETTINGS_EDIT)
    cleaned = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(f"Неизвестная настройка: {key}")
        if key == "pricing.default_markup" and value not in (None, ""):
            value = str(validate_markup(value))
        elif key == "pricing.labor_rate" and value not in (None, ""):
            value = str(non_negative_money(value, "Стоимость нормо-часа"))
        elif key == "tax.vat_rate" and value not in (None, ""):
            rate = D(value)
            if rate < 0 or rate > 100:
                from autoservice.domain.errors import ValidationError

                raise ValidationError("Ставка НДС должна быть от 0 до 100 %")
            value = str(rate)
        elif value == "" and DEFAULTS[key] is None:
            value = None
        cleaned[key] = value
    with ctx.db.transaction() as s:
        old = {k: get_setting(s, k) for k in cleaned}
        for key, value in cleaned.items():
            _store(s, key, value)
        audit(s, ctx, "settings.update", "settings", None, changes={k: [old[k], cleaned[k]] for k in cleaned})


def default_markup(s: Session) -> Decimal | None:
    value = get_setting(s, "pricing.default_markup")
    return None if value in (None, "") else validate_markup(value)


def read_settings(ctx: AppContext) -> dict:
    with ctx.db.read() as s:
        return get_all(s)
