"""Обязательные сценарии 1–21: склад, закупки, наценка, продажи, FIFO, клиенты, авто, заказ-наряды, оплаты, возвраты."""

from decimal import Decimal

import pytest

from autoservice.application import catalog, inventory, payments, purchasing, sales, stock_docs, vehicles, work_orders
from autoservice.domain.enums import PaymentKind, WOStatus
from autoservice.domain.errors import (
    AlreadyPosted,
    DuplicateDocument,
    InsufficientStock,
    InvalidStatusTransition,
    PermissionDenied,
    ValidationError,
)

D = lambda x: Decimal(str(x))  # noqa: E731


def stock(app, part_id):
    with app.db.read() as s:
        return inventory.on_hand(s, part_id), inventory.available(s, part_id)


def test_01_02_create_part_and_purchase_10(app, make_part, supplier):
    part = make_part()
    pid = purchasing.create_purchase(app, supplier, "ТН-100", supplier_doc_date="2026-11-01")
    purchasing.add_purchase_line(app, pid, part, "10", "650")
    assert stock(app, part) == (D("0.000"), D("0.000"))  # черновик не меняет склад
    purchasing.post_purchase(app, pid)
    assert stock(app, part) == (D("10.000"), D("10.000"))
    po = purchasing.get_purchase(app, pid)
    assert po["total"] == D("6500.00") and po["payment_status"] == "Не оплачено"


def test_purchase_cannot_be_posted_twice_and_duplicates_blocked(app, make_part, supplier):
    part = make_part()
    pid = purchasing.create_purchase(app, supplier, "ТН-7")
    purchasing.add_purchase_line(app, pid, part, "1", "100")
    purchasing.post_purchase(app, pid)
    with pytest.raises(AlreadyPosted):
        purchasing.post_purchase(app, pid)
    assert stock(app, part)[0] == D("1")
    with pytest.raises(DuplicateDocument):
        purchasing.create_purchase(app, supplier, "тн-7")
    with pytest.raises(AlreadyPosted):
        purchasing.add_purchase_line(app, pid, part, "1", "100")


@pytest.mark.parametrize("markup,price", [("0", "1000.00"), ("30", "1300.00"), ("100", "2000.00"), ("17.5", "1175.00")])
def test_03_06_markup_on_sale(app, make_part, purchase, markup, price):
    part = make_part()
    purchase([(part, "10", "1000")])
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, part, "1", markup_override=markup)
    sale = sales.get_sale(app, sid)
    assert sale["items"][0]["unit_price"] == D(price)
    assert sale["items"][0]["markup_pct"] == D(markup)
    assert sale["items"][0]["markup_source"] == "operation"


def test_markup_out_of_range_rejected(app, make_part, purchase):
    part = make_part()
    purchase([(part, "1", "1000")])
    sid = sales.create_sale(app)
    for bad in ("101", "-1"):
        with pytest.raises(ValidationError):
            sales.add_sale_line(app, sid, part, "1", markup_override=bad)


def test_markup_priority_in_operations(app, make_part, purchase, setup_markup):
    part = make_part(markup="40")
    purchase([(part, "5", "1000")], markup="25")  # наценка партии
    p = sales.preview_line(app, part, "1")
    assert (p["markup_pct"], p["markup_source"], p["unit_price"]) == (D("25"), "batch", D("1250.00"))
    other = make_part(name="Свеча", oem="SP-1", markup="40")
    purchase([(other, "5", "1000")])
    assert sales.preview_line(app, other, "1")["markup_source"] == "part"
    third = make_part(name="Ремень", oem="R-1")
    purchase([(third, "5", "1000")])
    assert sales.preview_line(app, third, "1")["markup_source"] == "default"
    assert sales.preview_line(app, third, "1", markup_override="5")["unit_price"] == D("1050.00")


def test_no_markup_configured_requires_explicit_choice(app, make_part, purchase):
    part = make_part()
    purchase([(part, "1", "500")])
    sid = sales.create_sale(app)
    with pytest.raises(ValidationError, match="Наценка не задана"):
        sales.add_sale_line(app, sid, part, "1")


def test_07_10_partial_sale_second_price_fifo(app, make_part, purchase):
    part = make_part()
    purchase([(part, "10", "100")])  # партия 1: 10 × 100
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, part, "4", markup_override="50")
    sales.post_sale(app, sid)
    assert stock(app, part)[0] == D("6")  # 8. остаток
    sale = sales.get_sale(app, sid)
    assert sale["items"][0]["cogs"] == D("400.00")
    assert sale["gross_profit"] == D("200.00")

    purchase([(part, "10", "130")])  # 9. та же деталь по другой цене
    sid2 = sales.create_sale(app)
    sales.add_sale_line(app, sid2, part, "8", markup_override="0")
    preview_cost = sales.get_sale(app, sid2)["items"][0]["cost_basis_total"]
    assert preview_cost == D("860.00")  # 6 × 100 + 2 × 130
    sales.post_sale(app, sid2)
    sale2 = sales.get_sale(app, sid2)
    assert sale2["items"][0]["cogs"] == D("860.00")  # 10. FIFO
    assert stock(app, part)[0] == D("8")
    with app.db.read() as s:
        value, unknown = inventory.stock_value(s, part)
    assert value == D("1040.00") and unknown == 0  # 8 × 130


def test_extra_costs_allocated_to_batches(app, make_part, purchase):
    a = make_part(name="A", oem="A1")
    b = make_part(name="B", oem="B1")
    purchase([(a, "1", "100"), (b, "2", "100")], extra="30.00")
    with app.db.read() as s:
        assert inventory.stock_value(s, a)[0] == D("110.00")
        assert inventory.stock_value(s, b)[0] == D("220.00")


def test_sale_insufficient_stock_rolls_back(app, make_part, purchase):
    a = make_part(name="A", oem="A1")
    b = make_part(name="B", oem="B1")
    purchase([(a, "5", "10"), (b, "1", "10")])
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, a, "2", markup_override="10")
    sales.add_sale_line(app, sid, b, "3", markup_override="10")
    with pytest.raises(InsufficientStock):
        sales.post_sale(app, sid)
    assert stock(app, a)[0] == D("5")  # атомарность: ничего не списано
    assert sales.get_sale(app, sid)["status"] == "draft"


def test_sale_unknown_cost_requires_manual_price_and_no_fake_profit(app, make_part):
    part = make_part()
    stock_docs.enter_opening_balance(app, part, "3")  # остаток без себестоимости
    sid = sales.create_sale(app)
    with pytest.raises(ValidationError, match="Себестоимость"):
        sales.add_sale_line(app, sid, part, "1", markup_override="30")
    sales.add_sale_line(app, sid, part, "1", manual_unit_price="500")
    sales.post_sale(app, sid)
    sale = sales.get_sale(app, sid)
    assert sale["gross_profit"] is None and sale["cogs_complete"] is False


def test_11_13_customer_vehicle_and_search(app):
    cid = vehicles.save_customer(app, {"name": "Иванов Иван", "phone": "8 (999) 123-45-67"})
    vid, warnings = vehicles.create_vehicle(app, {"make": "Toyota", "model": "Camry", "plate": "А123ВС77", "year": 2018}, owner_id=cid)
    assert any("VIN" in w for w in warnings)
    vid2, warnings2 = vehicles.create_vehicle(app, {"make": "Kia", "model": "Rio", "vin": "XWEFF41BDA0001234"})
    assert warnings2 == ["Госномер не указан"]
    assert [v["id"] for v in vehicles.search_vehicles(app, "а123вс")] == [vid]
    assert [v["id"] for v in vehicles.search_vehicles(app, "A123BC77")] == [vid]  # латиница
    assert [v["id"] for v in vehicles.search_vehicles(app, "Camry")] == [vid]
    assert [v["id"] for v in vehicles.search_vehicles(app, "иванов")] == [vid]
    assert [v["id"] for v in vehicles.search_vehicles(app, "9991234567")] == [vid]
    assert [v["id"] for v in vehicles.search_vehicles(app, "XWEFF41")] == [vid2]
    vehicles.update_vehicle(app, vid, {"plate": "В777ОР99"})
    assert [v["id"] for v in vehicles.search_vehicles(app, "А123ВС77")] == [vid]  # по старому номеру
    with pytest.raises(ValidationError):
        vehicles.create_vehicle(app, {"make": "Kia", "vin": "XWEFF41BDA0001234"})


def _wo_setup(app, make_part, purchase):
    cid = vehicles.save_customer(app, {"name": "Петров Пётр", "phone": "+79990000001"})
    vid, _ = vehicles.create_vehicle(app, {"make": "Toyota", "model": "Camry", "plate": "А123ВС77"}, owner_id=cid)
    oil = make_part(name="Моторное масло 5W-30", oem="OIL-5W30", unit="л", category="fluids")
    filt = make_part(name="Масляный фильтр", oem="90915-YZZE1")
    purchase([(oil, "20", "500"), (filt, "5", "500")])
    return cid, vid, oil, filt


def test_14_18_work_order_full_cycle(app, make_part, purchase):
    cid, vid, oil, filt = _wo_setup(app, make_part, purchase)
    wo = work_orders.create_work_order(app, vid, complaint="Плановое ТО", mileage="85 000")
    work_orders.add_service(app, wo, "Замена моторного масла", "1000")  # 15
    r1 = work_orders.add_part(app, wo, oil, "4", markup_override="60")  # 16 + 17 резерв
    r2 = work_orders.add_part(app, wo, filt, "1", markup_override="30")
    assert r1["reserved"] == D("4") and r2["shortage"] == 0
    assert stock(app, oil) == (D("20"), D("16"))
    d = work_orders.get_work_order(app, wo)
    assert d["parts"][0]["unit_price"] == D("800.00")  # 500 × 1,6
    assert d["parts"][1]["unit_price"] == D("650.00")
    assert d["estimate_amount"] == D("4850.00")  # как в примере квитанции
    with pytest.raises(ValidationError):
        work_orders.change_status(app, wo, WOStatus.IN_PROGRESS) or work_orders.change_status(app, wo, WOStatus.READY)
    for p in d["parts"]:
        work_orders.issue_part(app, p["id"])  # 17 списание
    assert stock(app, oil) == (D("16"), D("16"))
    work_orders.set_service_done(app, d["services"][0]["id"])
    work_orders.change_status(app, wo, WOStatus.READY)
    work_orders.change_status(app, wo, WOStatus.ISSUED)  # 18 закрытие
    d = work_orders.get_work_order(app, wo)
    assert d["total_amount"] == D("4850.00")
    assert d["debt"] == D("4850.00")  # выдан ≠ оплачен
    assert d["cogs_amount"] == D("2500.00")  # 4 × 500 + 500
    assert d["gross_profit"] == D("2350.00")
    assert vehicles.get_vehicle(app, vid)["mileage"] == 85000
    with pytest.raises(ValidationError):
        work_orders.add_service(app, wo, "Ещё работа", "100")


def test_19_partial_payment(app, make_part, purchase):
    cid, vid, oil, filt = _wo_setup(app, make_part, purchase)
    wo = work_orders.create_work_order(app, vid)
    work_orders.add_service(app, wo, "Диагностика", "1500", done=True)
    payments.record_payment(app, kind=PaymentKind.CUSTOMER_PAYMENT, method="cash", amount="500", work_order_id=wo)
    with pytest.raises(ValidationError, match="превышает"):
        payments.record_payment(app, kind=PaymentKind.CUSTOMER_PAYMENT, method="card", amount="1000.01", work_order_id=wo)
    work_orders.change_status(app, wo, WOStatus.IN_PROGRESS)
    work_orders.change_status(app, wo, WOStatus.READY)
    work_orders.change_status(app, wo, WOStatus.ISSUED)
    d = work_orders.get_work_order(app, wo)
    assert (d["paid"], d["debt"]) == (D("500.00"), D("1000.00"))
    payments.record_payment(app, kind=PaymentKind.CUSTOMER_PAYMENT, method="card", amount="1000", work_order_id=wo)
    assert work_orders.get_work_order(app, wo)["debt"] == D("0.00")


def test_20_return_from_customer_restores_fifo_cost(app, make_part, purchase):
    part = make_part()
    purchase([(part, "2", "100")])
    purchase([(part, "2", "200")])
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, part, "3", markup_override="50", discount_amount="10")
    sales.post_sale(app, sid, payment_method="cash")
    sale = sales.get_sale(app, sid)
    assert sale["items"][0]["cogs"] == D("400.00") and sale["paid"] == sale["total_net"]
    rid = sales.create_return(app, sid, [(sale["items"][0]["id"], "1")], "Не подошла", refund_method="cash")
    sale = sales.get_sale(app, sid)
    assert stock(app, part)[0] == D("2")
    with app.db.read() as s:
        assert inventory.stock_value(s, part)[0] == D("400.00")  # вернулась единица по 200
    assert sale["returned"] == D("196.67")  # (3 × 200 − 10) / 3
    assert sale["paid"] == sale["total_net"] - D("196.67")
    with pytest.raises(ValidationError):
        sales.create_return(app, sid, [(sale["items"][0]["id"], "3")], "Слишком много")


def test_21_cancel_erroneous_operations(app, manager, make_part, purchase, supplier):
    part = make_part()
    pid = purchase([(part, "5", "100")])
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, part, "2", markup_override="10")
    sales.post_sale(app, sid)
    with pytest.raises(PermissionDenied):
        sales.cancel_sale(manager, sid, "ошибка")
    with pytest.raises(ValidationError):
        purchasing.cancel_purchase(app, pid, "ошибка")  # товар уже продан
    sales.cancel_sale(app, sid, "Ошибочный ввод")
    assert stock(app, part)[0] == D("5")
    assert sales.get_sale(app, sid)["status"] == "cancelled"
    purchasing.cancel_purchase(app, pid, "Ошибочная закупка")
    assert stock(app, part)[0] == D("0")
    kinds = [m["kind"] for m in reversed(stock_docs.part_movements(app, part))]
    # история не удаляется: приход, продажа, отмена продажи, отмена закупки
    assert kinds == ["purchase_in", "sale_out", "sale_cancel_in", "purchase_cancel_out"]


def test_payment_cancel_keeps_history(app, make_part, purchase):
    part = make_part()
    purchase([(part, "1", "100")])
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, part, "1", markup_override="0")
    sales.post_sale(app, sid, payment_method="card")
    pay = payments.list_payments(app)[0]
    payments.cancel_payment(app, pay["id"], "Ошибка кассира")
    rows = payments.list_payments(app)
    assert rows[0]["status"] == "cancelled" and rows[0]["cancel_reason"] == "Ошибка кассира"
    assert sales.get_sale(app, sid)["debt"] == D("100.00")


def test_supplier_payment_and_return(app, make_part, purchase):
    part = make_part()
    pid = purchase([(part, "10", "100")])
    payments.record_payment(app, kind=PaymentKind.SUPPLIER_PAYMENT, method="transfer", amount="400", purchase_id=pid)
    po = purchasing.get_purchase(app, pid)
    assert po["payment_status"] == "Частично оплачено" and po["outstanding"] == D("600.00")
    purchasing.supplier_return(app, pid, [(po["items"][0]["id"], "2")], "Брак")
    po = purchasing.get_purchase(app, pid)
    assert po["outstanding"] == D("400.00") and stock(app, part)[0] == D("8")
    with pytest.raises(ValidationError):
        payments.record_payment(app, kind=PaymentKind.SUPPLIER_PAYMENT, method="cash", amount="401", purchase_id=pid)


def test_wo_parts_return_and_cancel(app, make_part, purchase):
    cid, vid, oil, filt = _wo_setup(app, make_part, purchase)
    wo = work_orders.create_work_order(app, vid)
    line = work_orders.add_part(app, wo, filt, "2", markup_override="10")["id"]
    work_orders.issue_part(app, line)
    assert stock(app, filt)[0] == D("3")
    with pytest.raises(ValidationError, match="верните"):
        work_orders.change_status(app, wo, WOStatus.CANCELLED, "Клиент отказался")
    work_orders.return_part(app, line, "2", "Не понадобилась")
    assert stock(app, filt)[0] == D("5")
    work_orders.change_status(app, wo, WOStatus.CANCELLED, "Клиент отказался")
    with pytest.raises(InvalidStatusTransition):
        work_orders.change_status(app, wo, WOStatus.IN_PROGRESS)


def test_reservation_blocks_sale_of_reserved_stock(app, make_part, purchase):
    cid, vid, oil, filt = _wo_setup(app, make_part, purchase)
    wo = work_orders.create_work_order(app, vid)
    work_orders.add_part(app, wo, filt, "4", markup_override="10")
    sid = sales.create_sale(app)
    sales.add_sale_line(app, sid, filt, "2", markup_override="10")
    with pytest.raises(InsufficientStock):
        sales.post_sale(app, sid)


def test_inventory_shortage_and_surplus(app, make_part, purchase):
    a = make_part(name="A", oem="A1")
    b = make_part(name="B", oem="B1")
    purchase([(a, "10", "50"), (b, "2", "10")])
    cnt = stock_docs.create_inventory(app, [a, b])
    inv = stock_docs.get_inventory(app, cnt)
    ia = next(i for i in inv["items"] if i["part_id"] == a)
    ib = next(i for i in inv["items"] if i["part_id"] == b)
    stock_docs.set_actual(app, ia["id"], "8")
    stock_docs.set_actual(app, ib["id"], "3", "12")
    res = stock_docs.post_inventory(app, cnt)
    assert res["shortage_cost"] == D("100.00") and res["surplus_lines"] == 1
    assert stock(app, a)[0] == D("8") and stock(app, b)[0] == D("3")


def test_manager_permissions(manager, app):
    from autoservice.application import finance
    from autoservice.application.settings_service import update_settings

    with pytest.raises(PermissionDenied):
        update_settings(manager, {"pricing.default_markup": "20"})
    with pytest.raises(PermissionDenied):
        finance.add_expense(manager, "Аренда", "1000", "cash")
    catalog.create_part(manager, {"name": "Щётка"})  # операционные права есть
