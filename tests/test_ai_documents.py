"""Сценарии 26–27 и ИИ-функции: накладные, дубли, ошибки OCR, помощник, рекомендации, LLM-адаптер."""

from decimal import Decimal

import pytest
from openpyxl import Workbook

from autoservice.ai import invoice_import, invoice_parser, llm, recommendations
from autoservice.ai.assistant import DiagnosisInput, analyze
from autoservice.application import catalog, inventory, purchasing, sales
from autoservice.domain.enums import Confidence, Verification
from autoservice.domain.errors import DomainError, ValidationError
from autoservice.knowledge import library, rules

from helpers import populate

INVOICE_TEXT = """ООО «Автодеталь»
Поставщик: ООО «Автодеталь», ИНН 7700000001, г. Москва
Счёт на оплату № 1532 от 05.11.2026
№ Артикул Наименование Кол-во Ед. Цена Сумма
1 90915-YZZE1 Фильтр масляный Toyota 10 шт 520,00 5 200,00
2 OIL-5W30 Масло моторное 5W-30 20 л 480,50 9 610,00
3 NEW-777 Колодки тормозные передние 2 компл 3 150,00 6 300,00
Итого: 21 110,00
Всего к оплате: 21 110,00
"""


def test_parse_text_invoice():
    inv = invoice_parser.parse_text(INVOICE_TEXT)
    assert inv.doc_number == "1532" and str(inv.doc_date) == "2026-11-05"
    assert inv.supplier_inn == "7700000001"
    assert [(ln.article, ln.qty, ln.price, ln.amount) for ln in inv.lines] == [
        ("90915-YZZE1", Decimal("10"), Decimal("520.00"), Decimal("5200.00")),
        ("OIL-5W30", Decimal("20"), Decimal("480.50"), Decimal("9610.00")),
        ("NEW-777", Decimal("2"), Decimal("3150.00"), Decimal("6300.00")),
    ]
    assert inv.total_declared == Decimal("21110.00") and not inv.warnings


def test_ocr_errors_are_flagged_not_hidden():
    bad = INVOICE_TEXT.replace("5 200,00", "5 800,00").replace("Итого: 21 110,00", "Итого: 21 710,00")
    inv = invoice_parser.parse_text(bad, ocr_used=True)
    first = inv.lines[0]
    assert not first.arithmetic_ok and any("Арифметика не сходится" in f for f in first.flags)
    assert all(any("OCR" in f for f in ln.flags) for ln in inv.lines)
    wrong_total = INVOICE_TEXT.replace("Всего к оплате: 21 110,00", "Всего к оплате: 99 999,00")
    assert any("не совпадает с итогом" in w for w in invoice_parser.parse_text(wrong_total).warnings)


def test_26_invalid_invoice_handled(app, tmp_path):
    junk = tmp_path / "broken.pdf"
    junk.write_bytes(b"%PDF-1.4 this is not really a pdf")
    imp = invoice_import.get_import(app, invoice_import.import_invoice(app, junk))
    assert imp["status"] == "failed" and imp["warnings"]
    empty = tmp_path / "empty.txt"
    empty.write_text("Просто текст без таблицы", encoding="utf-8")
    imp2 = invoice_import.get_import(app, invoice_import.import_invoice(app, empty))
    assert imp2["status"] == "needs_review" and any("не распознаны" in w for w in imp2["warnings"])
    with pytest.raises(ValidationError):
        invoice_import.confirm_import(app, imp2["id"])
    exe = tmp_path / "virus.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(ValidationError, match="не поддерживается"):
        invoice_import.import_invoice(app, exe)


def test_invoice_text_import_to_draft_purchase(app, tmp_path):
    data = populate(app)
    f = tmp_path / "invoice.txt"
    f.write_text(INVOICE_TEXT, encoding="utf-8")
    imp_id = invoice_import.import_invoice(app, f)
    imp = invoice_import.get_import(app, imp_id)
    assert imp["status"] == "needs_review" and imp["supplier_id"] == data["supplier"]
    statuses = [ln["match_status"] for ln in imp["lines"]]
    assert statuses == ["found", "found", "not_found"]
    with pytest.raises(ValidationError, match="Не сопоставлены"):
        invoice_import.confirm_import(app, imp_id)
    invoice_import.update_line(app, imp_id, 2, skip=True)
    before = catalog.get_part(app, data["filter"])["on_hand"]
    pid = invoice_import.confirm_import(app, imp_id)
    po = purchasing.get_purchase(app, pid)
    assert po["status"] == "draft" and po["total"] == Decimal("14810.00") and po["invoice_document_id"]
    assert catalog.get_part(app, data["filter"])["on_hand"] == before  # склад не изменился до проведения
    stored = invoice_import.get_import(app, imp_id)
    from autoservice.application.documents_store import document_file

    assert document_file(app, stored["document_id"]).read_text(encoding="utf-8") == INVOICE_TEXT  # оригинал сохранён


def test_27_repeated_import_of_same_document(app, tmp_path):
    populate(app)
    f = tmp_path / "invoice.txt"
    f.write_text(INVOICE_TEXT, encoding="utf-8")
    first = invoice_import.import_invoice(app, f)
    invoice_import.update_line(app, first, 2, skip=True)
    invoice_import.confirm_import(app, first)
    second = invoice_import.get_import(app, invoice_import.import_invoice(app, f))
    assert second["status"] == "duplicate" and second["lines"] == []
    copy = tmp_path / "invoice-copy.txt"
    copy.write_text(INVOICE_TEXT + "\n", encoding="utf-8")  # другой файл, тот же документ поставщика
    third = invoice_import.get_import(app, invoice_import.import_invoice(app, copy))
    assert any("уже внесён" in w for w in third["warnings"])
    invoice_import.update_line(app, third["id"], 2, skip=True)
    from autoservice.domain.errors import DuplicateDocument

    with pytest.raises(DuplicateDocument):
        invoice_import.confirm_import(app, third["id"])


def test_xlsx_invoice(app, tmp_path):
    data = populate(app)
    wb = Workbook()
    ws = wb.active
    ws.append(["Товарная накладная № Т-88 от 03.11.2026"])
    ws.append(["Поставщик: ООО «Автодеталь», ИНН 7700000001"])
    ws.append([])
    ws.append(["№", "Артикул", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"])
    ws.append([1, "90915-YZZE1", "Фильтр масляный", 4, "шт", 510, 2040])
    ws.append([2, "OIL-5W30", "Масло 5W-30", 5, "л", "490,00", "2 450,00"])
    ws.append(["Итого", None, None, None, None, None, 4490])
    path = tmp_path / "nakl.xlsx"
    wb.save(path)
    imp = invoice_import.get_import(app, invoice_import.import_invoice(app, path))
    assert imp["doc_number"] == "Т-88" and imp["total_calculated"] == Decimal("4490.00")
    assert [ln["match_status"] for ln in imp["lines"]] == ["found", "found"]


def test_name_similarity_never_auto_matches(app, tmp_path):
    populate(app)
    f = tmp_path / "inv.txt"
    f.write_text("Счёт № 5 от 01.11.2026\n1 ZZZ-1 Масляный фильтр 1 шт 100,00 100,00\nИтого: 100,00\n", encoding="utf-8")
    imp = invoice_import.get_import(app, invoice_import.import_invoice(app, f))
    line = imp["lines"][0]
    assert line["part_id"] is None and line["candidates"]
    assert any("Сомнительное совпадение" in x for x in line["flags"])


def test_assistant_levels_and_no_invented_parts(app):
    data = populate(app)
    res = analyze(app, DiagnosisInput(make="Toyota", model="Camry", year=2018, complaint="Горит чек, ошибка P0171, плавают обороты"))
    assert "P0171" in res["codes"]
    assert any(h["dtc_code"] == "P0171" and h["level"] == Confidence.NEEDS_CHECK for h in res["hypotheses"])
    assert res["history"] and res["history"][0]["level"] == Confidence.CONFIRMED  # заказ-наряд с P0171 выдан
    assert res["parts"] == []  # применимость не указана → детали не предлагаются
    assert any("нет запчастей категории" in w for w in res["warnings"])
    catalog.add_compatibility(app, data["filter"], {"make": "Toyota", "model": "Camry", "source": "Каталог Toyota EPC",
                                                    "verification_status": Verification.CONFIRMED})
    res2 = analyze(app, DiagnosisInput(make="Toyota", model="Camry", complaint="течь масла из-под фильтра, лампа давления масла"))
    assert any(p["part_id"] == data["filter"] and p["level"] == Confidence.CONFIRMED for p in res2["parts"])
    assert res2["warnings"] and "профессиональная проверка" in " ".join(res2["warnings"])


def test_assistant_insufficient_data(app):
    res = analyze(app, DiagnosisInput(complaint="что-то странное"))
    assert res["overall"] == Confidence.INSUFFICIENT
    assert "Марка автомобиля" in res["missing_info"]


def test_brakes_are_critical(app):
    res = analyze(app, DiagnosisInput(make="Kia", model="Rio", complaint="скрип при торможении, педаль тормоза мягкая"))
    assert any("безопасность" in w for w in res["warnings"])


def test_library_search_and_user_rule(app, tmp_path):
    doc = tmp_path / "regl.txt"
    doc.write_text("Регламент: замена ремня ГРМ двигателя 2AR-FE не требуется, используется цепь.", encoding="utf-8")
    eid, warnings = library.add_document(app, doc, {"title": "Регламент Camry", "source": "Сервисная книжка владельца",
                                                     "make": "Toyota", "model": "Camry", "kind": "regulation"})
    assert not warnings
    hits = library.search(app, "цепь ГРМ", make="Toyota")
    assert hits and hits[0]["id"] == eid and "«" in hits[0]["snippet"]
    assert library.search(app, "цепь ГРМ", make="BMW") == []
    with pytest.raises(ValidationError):
        rules.save_rule(app, {"title": "Без источника"})
    rid = rules.save_rule(app, {"title": "Хруст ШРУСа", "keywords": "хруст при повороте", "source": "Опыт мастера",
                                "possible_causes": "Износ наружного ШРУСа", "checks": "Осмотр пыльника", "part_category_codes": "transmission"})
    res = analyze(app, DiagnosisInput(make="Lada", model="Vesta", complaint="хруст при повороте руля на месте"))
    assert any(h["rule_id"] == rid for h in res["hypotheses"])


def test_purchase_recommendations_and_drafts(app):
    data = populate(app)
    recs = recommendations.purchase_recommendations(app)
    by_part = {r["part_id"]: r for r in recs}
    assert data["oil"] in by_part and data["filter"] in by_part
    oil = by_part[data["oil"]]
    assert oil["available"] == Decimal("4") and oil["recommended_qty"] >= Decimal("6")
    assert oil["reliability"] == "Недостаточно данных для прогноза" and "минимальный остаток" in oil["basis"]
    assert oil["last_price"] == Decimal("500.00") and oil["supplier_id"] == data["supplier"]
    before = catalog.get_part(app, data["oil"])["on_hand"]
    ids = recommendations.create_purchase_drafts(app, [oil])
    assert purchasing.get_purchase(app, ids[0])["status"] == "draft"
    assert catalog.get_part(app, data["oil"])["on_hand"] == before


def test_llm_adapter_is_local_only_and_honest(app):
    with pytest.raises(DomainError, match="только адреса этого компьютера"):
        llm.check_local_url("http://api.example.com:11434")
    with pytest.raises(DomainError):
        llm.check_local_url("http://192.168.1.10:11434")
    assert llm.check_local_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    model = llm.LocalLLM("ollama", "http://127.0.0.1:9", "qwen2.5:7b")  # порт 9 — ничего не слушает
    st = model.status()
    assert not st.available and "недоступна" in st.text
    with pytest.raises(DomainError, match="выключен"):
        llm.ask_llm(app, {}, {})
