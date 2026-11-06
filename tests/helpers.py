"""Общие сценарии наполнения тестовой базы."""

from decimal import Decimal

from autoservice.application import catalog, purchasing, sales, vehicles, work_orders
from autoservice.domain.enums import PaymentKind, WOStatus


def populate(ctx):
    """Реалистичный набор: поставщик, 2 запчасти, закупка, продажа, закрытый заказ-наряд с оплатой."""
    from autoservice.application import payments

    sup = catalog.save_supplier(ctx, {"name": "ООО «Автодеталь»", "inn": "7700000001", "lead_time_days": 2})
    with ctx.db.read() as s:
        fluids = catalog.category_by_code(s, "fluids").id
        filters = catalog.category_by_code(s, "filters").id
    oil = catalog.create_part(ctx, {"name": "Моторное масло 5W-30", "oem_number": "OIL-5W30", "unit": "л", "category_id": fluids,
                                    "min_stock": "10"})
    flt = catalog.create_part(ctx, {"name": "Масляный фильтр", "oem_number": "90915-YZZE1", "brand": "Toyota", "category_id": filters,
                                    "min_stock": "2"})
    pid = purchasing.create_purchase(ctx, sup, "УПД-15", supplier_doc_date="2026-11-01")
    purchasing.add_purchase_line(ctx, pid, oil, "8", "500")
    purchasing.add_purchase_line(ctx, pid, flt, "3", "500")
    purchasing.post_purchase(ctx, pid)
    sid = sales.create_sale(ctx, customer_name="Розничный покупатель")
    sales.add_sale_line(ctx, sid, flt, "1", markup_override="30")
    sales.post_sale(ctx, sid, payment_method="cash")
    cid = vehicles.save_customer(ctx, {"name": "Иванов Иван Иванович", "phone": "+7 999 111-22-33"})
    vid, _ = vehicles.create_vehicle(ctx, {"make": "Toyota", "model": "Camry", "year": 2018, "plate": "А123ВС77",
                                           "vin": "JTNBE46K803012345", "engine_code": "2AR-FE", "mileage": "85000"}, owner_id=cid)
    wo = work_orders.create_work_order(ctx, vid, complaint="Плановое ТО, горит P0171", mileage="86000")
    work_orders.add_service(ctx, wo, "Замена моторного масла", "1000", done=True)
    work_orders.add_part(ctx, wo, oil, "4", markup_override="60")
    work_orders.add_part(ctx, wo, flt, "1", markup_override="30")
    for p in work_orders.get_work_order(ctx, wo)["parts"]:
        work_orders.issue_part(ctx, p["id"])
    work_orders.update_work_order(ctx, wo, diagnosis="Подсос воздуха после ДМРВ, заменён хомут")
    work_orders.change_status(ctx, wo, WOStatus.READY)
    work_orders.change_status(ctx, wo, WOStatus.ISSUED)
    payments.record_payment(ctx, kind=PaymentKind.CUSTOMER_PAYMENT, method="card", amount="3000", work_order_id=wo)
    return {"supplier": sup, "oil": oil, "filter": flt, "purchase": pid, "sale": sid, "customer": cid, "vehicle": vid, "wo": wo,
            "wo_total": Decimal("4850.00")}
