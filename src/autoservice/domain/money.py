"""Денежная модель.

Правила (см. docs/CALCULATIONS.md):
* все суммы — ``Decimal``; ``float`` запрещён и вызывает ``TypeError``;
* в базе суммы хранятся целым числом копеек, количество — тысячными долями,
  проценты — сотыми долями процента;
* округление до копейки — математическое «половина вверх» (ROUND_HALF_UP).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from autoservice.domain.errors import ValidationError

CENT = Decimal("0.01")
QTY_STEP = Decimal("0.001")
PCT_STEP = Decimal("0.01")
ZERO = Decimal("0")
NBSP = " "


def D(value) -> Decimal:
    """Преобразует ввод в Decimal. Принимает Decimal, int, str ("1 234,50")."""
    if value is None:
        raise ValidationError("Не указано числовое значение")
    if isinstance(value, bool):
        raise TypeError("bool не является числом")
    if isinstance(value, float):
        raise TypeError("float запрещён для денежных расчётов — используйте Decimal или строку")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        text = value.strip().replace(NBSP, "").replace(" ", "").replace("₽", "").replace(",", ".")
        if not text:
            raise ValidationError("Пустое числовое значение")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise ValidationError(f"Некорректное число: «{value}»") from exc
    else:
        raise TypeError(f"Неподдерживаемый тип числа: {type(value).__name__}")
    if not result.is_finite():
        raise ValidationError("Число должно быть конечным")
    return result


def money(value) -> Decimal:
    """Округляет сумму до копейки (половина вверх)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
    q = D(value)
    rounded = q.quantize(QTY_STEP, rounding=ROUND_HALF_UP)
    if rounded != q:
        raise ValidationError("Количество допускает не более трёх знаков после запятой")
    return rounded


def positive_qty(value) -> Decimal:
    q = qty(value)
    if q <= 0:
        raise ValidationError("Количество должно быть больше нуля")
    return q


def non_negative_money(value, what: str = "Сумма") -> Decimal:
    m = D(value)
    if m != m.quantize(CENT, rounding=ROUND_HALF_UP):
        raise ValidationError(f"{what}: не более двух знаков после запятой")
    m = m.quantize(CENT)
    if m < 0:
        raise ValidationError(f"{what} не может быть отрицательной")
    return m


def positive_money(value, what: str = "Сумма") -> Decimal:
    m = non_negative_money(value, what)
    if m <= 0:
        raise ValidationError(f"{what} должна быть больше нуля")
    return m


def proportional(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    """Доля суммы ``total`` пропорционально ``part / whole``, до копейки."""
    if whole == 0:
        return ZERO.quantize(CENT)
    return money(total * part / whole)


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Распределяет сумму по весам без потери копеек (метод наибольших остатков)."""
    total = money(total)
    if not weights:
        return []
    weight_sum = sum(weights, ZERO)
    if weight_sum <= 0:
        # равное распределение
        weights = [Decimal(1)] * len(weights)
        weight_sum = Decimal(len(weights))
    cents_total = int(total * 100)
    raw = [Decimal(cents_total) * w / weight_sum for w in weights]
    floors = [int(r) for r in raw]  # веса неотрицательны, int() = floor
    remainder = cents_total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i]), reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return [(Decimal(c) / 100).quantize(CENT) for c in floors]


def _group(int_part: str) -> str:
    out = []
    while len(int_part) > 3:
        out.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    out.insert(0, int_part)
    return NBSP.join(out)


def fmt_money(value, currency: bool = True) -> str:
    if value is None:
        return "—"
    m = money(value)
    sign = "-" if m < 0 else ""
    int_part, frac = f"{abs(m):.2f}".split(".")
    text = f"{sign}{_group(int_part)},{frac}"
    return f"{text}{NBSP}₽" if currency else text


def fmt_money_short(value) -> str:
    """Без копеек, если они нулевые: «1 200 ₽», иначе «1 200,50 ₽»."""
    if value is None:
        return "—"
    m = money(value)
    if m == m.to_integral_value():
        sign = "-" if m < 0 else ""
        return f"{sign}{_group(str(abs(int(m))))}{NBSP}₽"
    return fmt_money(m)


def fmt_qty(value) -> str:
    if value is None:
        return "—"
    q = D(value).quantize(QTY_STEP)
    text = format(q.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def fmt_percent(value) -> str:
    if value is None:
        return "—"
    p = D(value).quantize(PCT_STEP)
    text = format(p.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')}%"
