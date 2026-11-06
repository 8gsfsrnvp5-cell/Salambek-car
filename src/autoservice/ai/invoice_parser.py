"""Разбор накладных/счетов поставщиков из текста и таблиц.

Результат — только черновые значения с флагами проверки. Ничего не
проводится автоматически: каждую позицию подтверждает менеджер.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from autoservice.domain.money import money
from autoservice.domain.normalize import normalize_text

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8, "сентябр": 9,
          "октябр": 10, "ноябр": 11, "декабр": 12}
UNITS_RE = r"(?:шт\.?|штук[аи]?|л\.?|литр[аов]*|кг\.?|м\.?|компл\.?|к-т|пар[аы]?|уп\.?)"
AMOUNT_RE = r"\d{1,3}(?:[  ]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2})"
LINE_RE = re.compile(
    rf"^\s*(?:(?P<no>\d{{1,3}})[.)]?\s+)?(?P<body>.+?)\s+(?P<qty>\d+(?:[.,]\d{{1,3}})?)\s*(?P<unit>{UNITS_RE})?\s+"
    rf"(?P<price>{AMOUNT_RE})\s+(?P<amount>{AMOUNT_RE})\s*(?:руб\.?|₽)?\s*$",
    re.IGNORECASE,
)
ARTICLE_TOKEN = re.compile(r"^(?=[^\s]*\d)[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9\-./]{2,}$")
DTC_LIKE = re.compile(r"^\d{1,2}[.,]\d{2}$")


@dataclass
class ParsedLine:
    line_no: int
    article: str
    name: str
    qty: Decimal | None
    unit: str
    price: Decimal | None
    amount: Decimal | None
    flags: list[str] = field(default_factory=list)

    @property
    def arithmetic_ok(self) -> bool:
        if self.qty is None or self.price is None or self.amount is None:
            return False
        return abs(money(self.qty * self.price) - self.amount) <= Decimal("0.01")


@dataclass
class ParsedInvoice:
    supplier_name: str = ""
    supplier_inn: str = ""
    doc_number: str = ""
    doc_date: date | None = None
    total_declared: Decimal | None = None
    lines: list[ParsedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_calculated(self) -> Decimal:
        return money(sum((ln.amount for ln in self.lines if ln.amount is not None), Decimal(0)))

    def to_json_lines(self) -> list[dict]:
        out = []
        for ln in self.lines:
            d = asdict(ln)
            for k in ("qty", "price", "amount"):
                d[k] = str(d[k]) if d[k] is not None else None
            d["arithmetic_ok"] = ln.arithmetic_ok
            out.append(d)
        return out


def parse_number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    t = str(text).strip().replace(" ", "").replace(" ", "").replace("₽", "").replace("руб.", "").replace("руб", "")
    if not t:
        return None
    if t.count(",") == 1 and t.count(".") >= 1:  # 1.234,56
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        value = Decimal(t)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_date(text: str) -> date | None:
    text = text.strip()
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text.lower())
    if m:
        d, word, y = int(m.group(1)), m.group(2), int(m.group(3))
        for stem, mo in MONTHS.items():
            if word.startswith(stem) and not (stem == "ма" and word.startswith("мар")):
                try:
                    return date(y, mo, d)
                except ValueError:
                    return None
    return None


def _header(text: str, inv: ParsedInvoice) -> None:
    low = text.lower()
    m = re.search(r"(?:сч[её]т[\s-]*(?:фактура|на оплату)?|накладная|упд|торг-?12|универсальный передаточный документ)[^\n№#]{0,60}[№#]\s*([\w\-/]+)"
                  r"(?:\s*от\s*([^\n]{6,25}))?", low)
    if not m:
        m = re.search(r"[№#]\s*([\w\-/]+)\s+от\s+([^\n]{6,25})", low)
    if m:
        start = m.start(1)
        inv.doc_number = text[start:start + len(m.group(1))].strip()
        if m.group(2):
            inv.doc_date = parse_date(m.group(2))
    sup = re.search(r"(?:поставщик|продавец|грузоотправитель|исполнитель)\s*[:\-]?\s*([^\n]+)", text, re.IGNORECASE)
    if sup:
        name = re.split(r",?\s*ИНН", sup.group(1), flags=re.IGNORECASE)[0].strip(" ,;")
        inv.supplier_name = name[:300]
        inn = re.search(r"ИНН\s*[:/]?\s*(\d{10}|\d{12})", text[sup.start():sup.start() + 400], re.IGNORECASE)
        if inn:
            inv.supplier_inn = inn.group(1)
    if not inv.supplier_inn:
        inn = re.search(r"ИНН\s*[:/]?\s*(\d{10}|\d{12})", text, re.IGNORECASE)
        if inn:
            inv.supplier_inn = inn.group(1)
    totals = re.findall(rf"(всего\s+к\s+оплате|итого\s+к\s+оплате|итого|всего)\s*[:\-]?\s*({AMOUNT_RE})", low)
    if totals:
        preferred = [t for t in totals if "оплат" in t[0]] or totals
        inv.total_declared = parse_number(preferred[-1][1])


def _split_body(body: str) -> tuple[str, str]:
    tokens = body.split()
    if tokens and ARTICLE_TOKEN.match(tokens[0]) and not DTC_LIKE.match(tokens[0]):
        return tokens[0], " ".join(tokens[1:])
    if len(tokens) > 1 and ARTICLE_TOKEN.match(tokens[-1]):
        return tokens[-1], " ".join(tokens[:-1])
    return "", body.strip()


def _finish(inv: ParsedInvoice, ocr_used: bool) -> ParsedInvoice:
    for ln in inv.lines:
        if ln.qty is None or ln.qty <= 0:
            ln.flags.append("Количество не распознано")
        if ln.price is None:
            ln.flags.append("Цена не распознана")
        if ln.amount is None:
            ln.flags.append("Сумма не распознана")
        if ln.qty is not None and ln.price is not None and ln.amount is not None and not ln.arithmetic_ok:
            ln.flags.append(f"Арифметика не сходится: {ln.qty} × {ln.price} ≠ {ln.amount}")
        if not ln.article:
            ln.flags.append("Артикул не найден в строке")
        if ocr_used:
            ln.flags.append("Значения получены OCR — сверьте с оригиналом")
    if not inv.lines:
        inv.warnings.append("Позиции не распознаны — заполните закупку вручную по оригиналу документа")
    if inv.total_declared is not None and inv.lines and inv.total_calculated != money(inv.total_declared):
        inv.warnings.append(f"Сумма строк {inv.total_calculated} не совпадает с итогом документа {money(inv.total_declared)}")
    if inv.total_declared is None and inv.lines:
        inv.warnings.append("Итоговая сумма документа не найдена — сверка итога невозможна")
    if not inv.doc_number:
        inv.warnings.append("Номер документа не найден")
    if not inv.supplier_inn and not inv.supplier_name:
        inv.warnings.append("Поставщик не определён")
    return inv


def parse_text(text: str, ocr_used: bool = False) -> ParsedInvoice:
    inv = ParsedInvoice()
    _header(text, inv)
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.match(r"^(итого|всего|в том числе|ндс)", line.lower()):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        article, name = _split_body(m.group("body"))
        if not name and not article:
            continue
        n += 1
        inv.lines.append(ParsedLine(
            line_no=int(m.group("no")) if m.group("no") else n, article=article, name=name,
            qty=parse_number(m.group("qty")), unit=(m.group("unit") or "").strip(". ").lower(),
            price=parse_number(m.group("price")), amount=parse_number(m.group("amount")),
        ))
    return _finish(inv, ocr_used)


HEADER_KEYS = {
    "article": ("артикул", "код товара", "код", "каталожный номер", "номер детали", "арт"),
    "name": ("наименование", "товар", "название", "наименование товара"),
    "qty": ("кол-во", "количество", "кол.", "колич", "кол"),
    "unit": ("ед.", "ед", "ед. изм.", "единица"),
    "price": ("цена", "цена за ед", "цена, руб", "цена руб"),
    "amount": ("сумма", "стоимость", "сумма, руб", "всего"),
}


def _header_map(row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        t = normalize_text(cell).strip(" :")
        for key, names in HEADER_KEYS.items():
            if key in mapping:
                continue
            if any(t == n or t.startswith(n + " ") or t.startswith(n + ",") for n in names):
                mapping[key] = idx
                break
    return mapping


def parse_table(rows: list[list[str]]) -> ParsedInvoice:
    inv = ParsedInvoice()
    text = "\n".join(" ".join(c for c in r if c) for r in rows)
    _header(text, inv)
    header_idx, mapping = None, {}
    for i, row in enumerate(rows[:50]):
        m = _header_map(row)
        if "name" in m and "qty" in m and ("price" in m or "amount" in m):
            header_idx, mapping = i, m
            break
    if header_idx is None:
        inv.warnings.append("Не найдена строка заголовков таблицы (Наименование, Количество, Цена, Сумма)")
        return _finish(inv, False)
    n = 0
    for row in rows[header_idx + 1:]:
        def cell(key):
            i = mapping.get(key)
            return row[i].strip() if i is not None and i < len(row) and row[i] is not None else ""

        name = cell("name")
        first = normalize_text(" ".join(row[:3]))
        if first.startswith(("итого", "всего")):
            break
        if not name:
            continue
        n += 1
        inv.lines.append(ParsedLine(line_no=n, article=cell("article"), name=name, qty=parse_number(cell("qty")),
                                    unit=cell("unit").strip(". ").lower(), price=parse_number(cell("price")),
                                    amount=parse_number(cell("amount"))))
    return _finish(inv, False)
