"""Клиенты и автомобили. Постоянный идентификатор автомобиля — внутренний id,
госномер может меняться (история номеров хранится)."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from autoservice.application.context import AppContext, audit, get_or_404
from autoservice.domain.enums import WOStatus
from autoservice.domain.errors import ValidationError
from autoservice.domain.money import qty as to_qty
from autoservice.domain.normalize import normalize_phone, normalize_plate, normalize_text, normalize_vin, vin_warning
from autoservice.persistence.models import Customer, Vehicle, VehiclePlateHistory, WorkOrder, now
from autoservice.security.permissions import Perm

# --------------------------------------------------------------------------- клиенты


def save_customer(ctx: AppContext, data: dict, customer_id: int | None = None) -> int:
    ctx.require(Perm.CUSTOMER_EDIT)
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Укажите имя клиента или название организации")
    kind = data.get("kind") or "person"
    if kind not in ("person", "company"):
        raise ValidationError("Тип клиента: частное лицо или организация")
    inn = (data.get("inn") or "").strip()
    if inn and (not inn.isdigit() or len(inn) not in (10, 12)):
        raise ValidationError("ИНН должен содержать 10 или 12 цифр")
    with ctx.db.transaction() as s:
        if customer_id:
            c = get_or_404(s, Customer, customer_id, "Клиент")
        else:
            c = Customer(name=name)
            s.add(c)
        c.kind = kind
        c.name = name
        c.name_search = normalize_text(name)
        c.phone = (data.get("phone") or "").strip()
        c.phone_normalized = normalize_phone(c.phone)
        c.email = (data.get("email") or "").strip()
        c.inn = inn
        c.address = (data.get("address") or "").strip()
        c.notes = (data.get("notes") or "").strip()
        s.flush()
        audit(s, ctx, "customer.update" if customer_id else "customer.create", "customer", c.id, name=name)
        return c.id


def customer_dict(c: Customer) -> dict:
    return {"id": c.id, "kind": c.kind, "name": c.name, "phone": c.phone, "email": c.email, "inn": c.inn,
            "address": c.address, "notes": c.notes}


def search_customers(ctx: AppContext, query: str = "", limit: int = 200) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Customer).order_by(Customer.name).limit(limit)
        q = (query or "").strip()
        if q:
            conds = [Customer.name_search.like(f"%{normalize_text(q)}%")]
            phone = normalize_phone(q)
            if len(phone) >= 4:
                conds.append(Customer.phone_normalized.like(f"%{phone[-10:]}%"))
            stmt = stmt.where(or_(*conds))
        return [customer_dict(c) for c in s.execute(stmt).scalars()]


def get_customer(ctx: AppContext, customer_id: int) -> dict:
    with ctx.db.read() as s:
        c = get_or_404(s, Customer, customer_id, "Клиент")
        d = customer_dict(c)
        d["vehicles"] = [vehicle_dict(v) for v in s.execute(select(Vehicle).where(Vehicle.owner_id == c.id)).scalars()]
        return d


# --------------------------------------------------------------------------- автомобили

VEHICLE_FIELDS = ("make", "model", "generation", "engine_type", "engine_code", "fuel", "transmission", "drive", "color", "notes")


def _clean_vehicle(data: dict) -> dict:
    out = {k: (data.get(k) or "").strip() for k in VEHICLE_FIELDS if k in data}
    if "make" in out and not out["make"]:
        raise ValidationError("Укажите марку автомобиля")
    if "year" in data:
        year = data.get("year")
        out["year"] = int(year) if year not in (None, "") else None
        if out["year"] is not None and not (1900 <= out["year"] <= now().year + 1):
            raise ValidationError("Некорректный год выпуска")
    if "mileage" in data:
        m = data.get("mileage")
        out["mileage"] = int(str(m).replace(" ", "")) if m not in (None, "") else None
        if out["mileage"] is not None and out["mileage"] < 0:
            raise ValidationError("Пробег не может быть отрицательным")
    if "engine_volume" in data:
        v = data.get("engine_volume")
        out["engine_volume"] = to_qty(str(v).replace(",", ".")) if v not in (None, "") else None
    return out


def create_vehicle(ctx: AppContext, data: dict, owner_id: int | None = None) -> tuple[int, list[str]]:
    """Быстрый ввод: достаточно марки. Возвращает (id, предупреждения)."""
    ctx.require(Perm.CUSTOMER_EDIT)
    clean = _clean_vehicle({"make": data.get("make", ""), **data})
    vin = normalize_vin(data.get("vin")) or None
    plate = (data.get("plate") or "").strip().upper()
    warnings = []
    w = vin_warning(vin)
    if w:
        warnings.append(w)
    if not plate:
        warnings.append("Госномер не указан")
    with ctx.db.transaction() as s:
        if vin and s.execute(select(Vehicle).where(Vehicle.vin == vin)).first():
            raise ValidationError(f"Автомобиль с VIN {vin} уже есть в базе — откройте его карточку")
        if owner_id:
            get_or_404(s, Customer, owner_id, "Клиент")
        v = Vehicle(vin=vin, plate=plate, plate_normalized=normalize_plate(plate), owner_id=owner_id, **clean)
        s.add(v)
        s.flush()
        if plate:
            s.add(VehiclePlateHistory(vehicle_id=v.id, plate=plate, plate_normalized=normalize_plate(plate)))
        audit(s, ctx, "vehicle.create", "vehicle", v.id, plate=plate, vin=vin or "")
        return v.id, warnings


def update_vehicle(ctx: AppContext, vehicle_id: int, data: dict) -> list[str]:
    ctx.require(Perm.CUSTOMER_EDIT)
    clean = _clean_vehicle(data)
    warnings = []
    with ctx.db.transaction() as s:
        v = get_or_404(s, Vehicle, vehicle_id, "Автомобиль")
        for k, val in clean.items():
            setattr(v, k, val)
        if "vin" in data:
            vin = normalize_vin(data.get("vin")) or None
            if vin and vin != v.vin and s.execute(select(Vehicle).where(Vehicle.vin == vin, Vehicle.id != v.id)).first():
                raise ValidationError(f"VIN {vin} уже принадлежит другому автомобилю")
            v.vin = vin
        if "owner_id" in data:
            if data["owner_id"]:
                get_or_404(s, Customer, data["owner_id"], "Клиент")
            v.owner_id = data["owner_id"] or None
        if "plate" in data:
            plate = (data.get("plate") or "").strip().upper()
            if normalize_plate(plate) != v.plate_normalized:
                for h in s.execute(select(VehiclePlateHistory).where(VehiclePlateHistory.vehicle_id == v.id, VehiclePlateHistory.valid_to.is_(None))).scalars():
                    h.valid_to = now()
                if plate:
                    s.add(VehiclePlateHistory(vehicle_id=v.id, plate=plate, plate_normalized=normalize_plate(plate)))
                v.plate = plate
                v.plate_normalized = normalize_plate(plate)
        w = vin_warning(v.vin)
        if w:
            warnings.append(w)
        audit(s, ctx, "vehicle.update", "vehicle", v.id)
    return warnings


def vehicle_title(v: Vehicle) -> str:
    parts = [v.make, v.model]
    if v.year:
        parts.append(str(v.year))
    return " ".join(p for p in parts if p)


def vehicle_dict(v: Vehicle) -> dict:
    return {
        "id": v.id, "vin": v.vin or "", "plate": v.plate, "make": v.make, "model": v.model, "generation": v.generation,
        "year": v.year, "engine_type": v.engine_type, "engine_code": v.engine_code, "engine_volume": v.engine_volume,
        "fuel": v.fuel, "transmission": v.transmission, "drive": v.drive, "mileage": v.mileage, "color": v.color,
        "owner_id": v.owner_id, "owner": v.owner.name if v.owner else "", "owner_phone": v.owner.phone if v.owner else "",
        "notes": v.notes, "title": vehicle_title(v), "vin_warning": vin_warning(v.vin),
    }


def search_vehicles(ctx: AppContext, query: str = "", limit: int = 200) -> list[dict]:
    """Поиск по госномеру (в т.ч. прежним), VIN, марке/модели, клиенту, номеру заказ-наряда."""
    with ctx.db.read() as s:
        q = (query or "").strip()
        stmt = select(Vehicle).order_by(Vehicle.updated_at.desc()).limit(limit)
        if q:
            plate = normalize_plate(q)
            vin = normalize_vin(q)
            text = f"%{normalize_text(q)}%"
            phone = normalize_phone(q)
            old_plates = select(VehiclePlateHistory.vehicle_id).where(VehiclePlateHistory.plate_normalized.like(f"%{plate}%"))
            by_customer_conds = [Customer.name_search.like(text)]
            if len(phone) >= 4:
                by_customer_conds.append(Customer.phone_normalized.like(f"%{phone[-10:]}%"))
            by_customer = select(Customer.id).where(or_(*by_customer_conds))
            by_wo = select(WorkOrder.vehicle_id).where(func.lower(WorkOrder.number).like(f"%{q.lower()}%"))
            make_model = func.lower(Vehicle.make + " " + Vehicle.model)
            stmt = stmt.where(
                or_(
                    Vehicle.plate_normalized.like(f"%{plate}%"),
                    Vehicle.id.in_(old_plates),
                    Vehicle.vin.like(f"%{vin}%"),
                    make_model.like(text),
                    func.lower(Vehicle.model).like(text),
                    Vehicle.owner_id.in_(by_customer),
                    Vehicle.id.in_(by_wo),
                )
            )
        return [vehicle_dict(v) for v in s.execute(stmt).scalars()]


def get_vehicle(ctx: AppContext, vehicle_id: int) -> dict:
    """Карточка автомобиля со всей историей обслуживания."""
    with ctx.db.read() as s:
        v = get_or_404(s, Vehicle, vehicle_id, "Автомобиль")
        d = vehicle_dict(v)
        d["plates"] = [
            {"plate": h.plate, "valid_from": h.valid_from, "valid_to": h.valid_to}
            for h in s.execute(select(VehiclePlateHistory).where(VehiclePlateHistory.vehicle_id == v.id).order_by(VehiclePlateHistory.valid_from)).scalars()
        ]
        history = []
        for wo in s.execute(select(WorkOrder).where(WorkOrder.vehicle_id == v.id).order_by(WorkOrder.opened_at.desc())).scalars():
            history.append({
                "id": wo.id, "number": wo.number, "opened_at": wo.opened_at, "closed_at": wo.closed_at, "status": wo.status,
                "status_title": WOStatus.TITLES.get(wo.status, wo.status), "mileage": wo.mileage, "complaint": wo.complaint,
                "diagnosis": wo.diagnosis, "total": wo.total_amount if wo.status == WOStatus.ISSUED else wo.estimate_amount,
                "services": [x.name for x in wo.services if x.is_done or wo.status not in (WOStatus.ISSUED,)],
                "parts": [f"{p.part.name} × {p.qty}" for p in wo.parts if p.qty > 0],
            })
        d["history"] = history
        return d
