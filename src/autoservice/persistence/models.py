"""Схема базы данных (SQLAlchemy 2.x).

Даты и время хранятся в местном времени компьютера автосервиса.
Деньги/количество/проценты — целые числа (см. persistence/types.py).
Проведённые операции не удаляются: отмена — смена статуса с причиной и
обратными движениями.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from autoservice.persistence.types import JSONText, Money, Percent, Qty

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


# --------------------------------------------------------------------------- система


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (CheckConstraint("role in ('admin','manager')", name="role"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSONText)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[object | None] = mapped_column(JSONText)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Sequence(Base):
    __tablename__ = "sequences"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class BackupHistory(Base):
    __tablename__ = "backup_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    path: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, default="")
    app_version: Mapped[str] = mapped_column(String(20), default="")
    schema_revision: Mapped[str] = mapped_column(String(40), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verify_status: Mapped[str | None] = mapped_column(String(20))


# --------------------------------------------------------------------------- клиенты и авто


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="person")
    name: Mapped[str] = mapped_column(String(300))
    name_search: Mapped[str] = mapped_column(String(300), default="", index=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    phone_normalized: Mapped[str] = mapped_column(String(20), default="", index=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    inn: Mapped[str] = mapped_column(String(12), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (CheckConstraint("kind in ('person','company')", name="kind"),)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(100), default="Механик")
    phone: Mapped[str] = mapped_column(String(50), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    vin: Mapped[str | None] = mapped_column(String(30), unique=True)
    plate: Mapped[str] = mapped_column(String(20), default="")
    plate_normalized: Mapped[str] = mapped_column(String(20), default="", index=True)
    make: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120), default="")
    generation: Mapped[str] = mapped_column(String(120), default="")
    year: Mapped[int | None] = mapped_column(Integer)
    engine_type: Mapped[str] = mapped_column(String(40), default="")
    engine_code: Mapped[str] = mapped_column(String(40), default="")
    engine_volume: Mapped[Decimal | None] = mapped_column(Qty)
    fuel: Mapped[str] = mapped_column(String(40), default="")
    transmission: Mapped[str] = mapped_column(String(40), default="")
    drive: Mapped[str] = mapped_column(String(40), default="")
    mileage: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(60), default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    owner: Mapped[Customer | None] = relationship()
    __table_args__ = (
        CheckConstraint("year is null or (year >= 1900 and year <= 2100)", name="year"),
        CheckConstraint("mileage is null or mileage >= 0", name="mileage"),
        Index("ix_vehicles_make_model", "make", "model"),
    )


class VehiclePlateHistory(Base):
    __tablename__ = "vehicle_plate_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    plate: Mapped[str] = mapped_column(String(20))
    plate_normalized: Mapped[str] = mapped_column(String(20), index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime)


# --------------------------------------------------------------------------- каталог


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    inn: Mapped[str] = mapped_column(String(12), default="", index=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PartCategory(Base):
    __tablename__ = "part_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("part_categories.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    """Файл в хранилище вложений. Оригиналы не изменяются и не удаляются."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    part_id: Mapped[int | None] = mapped_column(ForeignKey("parts.id", use_alter=True))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id", use_alter=True))
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id", use_alter=True))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", use_alter=True))


class Part(Base):
    """Справочная карточка запчасти. Остатки — в stock_batches."""

    __tablename__ = "parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    name_search: Mapped[str] = mapped_column(String(300), default="", index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("part_categories.id"), index=True)
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(200), default="")
    oem_number: Mapped[str] = mapped_column(String(80), default="")
    oem_number_normalized: Mapped[str] = mapped_column(String(80), default="", index=True)
    unit: Mapped[str] = mapped_column(String(20), default="шт")
    min_stock: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    location: Mapped[str] = mapped_column(String(100), default="")
    markup_pct: Mapped[Decimal | None] = mapped_column(Percent)
    photo_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    category: Mapped[PartCategory | None] = relationship()
    __table_args__ = (
        CheckConstraint("markup_pct is null or (markup_pct >= 0 and markup_pct <= 10000)", name="markup"),
        CheckConstraint("min_stock >= 0", name="min_stock"),
    )


class PartNumber(Base):
    """Альтернативные артикулы (OEM, аналоги производителей, артикулы поставщиков)."""

    __tablename__ = "part_numbers"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    number: Mapped[str] = mapped_column(String(80))
    number_normalized: Mapped[str] = mapped_column(String(80), index=True)
    brand: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="other")
    source: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(20), default="needs_check")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("part_id", "number_normalized", "brand", name="uq_part_numbers_part_number_brand"),)


class PartAnalog(Base):
    __tablename__ = "part_analogs"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    analog_part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    source: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(20), default="needs_check")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (
        UniqueConstraint("part_id", "analog_part_id", name="uq_part_analogs_pair"),
        CheckConstraint("part_id <> analog_part_id", name="not_self"),
    )


class PartCompatibility(Base):
    """Применимость запчасти. Всегда с источником и статусом проверки."""

    __tablename__ = "part_compatibility"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    make: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120), default="")
    generation: Mapped[str] = mapped_column(String(120), default="")
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    engine_code: Mapped[str] = mapped_column(String(40), default="")
    engine_volume: Mapped[Decimal | None] = mapped_column(Qty)
    fuel: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(20), default="needs_check")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (
        Index("ix_part_compat_make_model", "make", "model"),
        CheckConstraint("verification_status in ('confirmed','needs_check')", name="status"),
    )


class PartSupplier(Base):
    __tablename__ = "part_suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_article: Mapped[str] = mapped_column(String(80), default="")
    supplier_article_normalized: Mapped[str] = mapped_column(String(80), default="", index=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    last_price: Mapped[Decimal | None] = mapped_column(Money)
    last_price_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("part_id", "supplier_id", "supplier_article_normalized", name="uq_part_suppliers_link"),)


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    kind: Mapped[str] = mapped_column(String(30))  # purchase_cost | markup | sale_price
    amount: Mapped[Decimal | None] = mapped_column(Money)
    percent: Mapped[Decimal | None] = mapped_column(Percent)
    source_doc: Mapped[str] = mapped_column(String(100), default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


# --------------------------------------------------------------------------- закупки и склад


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_doc_number: Mapped[str] = mapped_column(String(80), default="")
    supplier_doc_date: Mapped[date | None] = mapped_column(Date)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    extra_costs: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    extra_costs_note: Mapped[str] = mapped_column(String(300), default="")
    total_goods: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="manual")
    invoice_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    supplier: Mapped[Supplier | None] = relationship()
    items: Mapped[list["PurchaseItem"]] = relationship(
        back_populates="purchase", order_by="PurchaseItem.line_no", cascade="all, delete-orphan"
    )
    __table_args__ = (
        CheckConstraint("status in ('draft','posted','cancelled')", name="status"),
        CheckConstraint("extra_costs >= 0", name="extra"),
        Index("ix_purchase_supplier_doc", "supplier_id", "supplier_doc_number"),
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount: Mapped[Decimal] = mapped_column(Money)
    allocated_extra: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    markup_pct: Mapped[Decimal | None] = mapped_column(Percent)
    supplier_article: Mapped[str] = mapped_column(String(80), default="")
    name_on_invoice: Mapped[str] = mapped_column(String(300), default="")

    purchase: Mapped[PurchaseOrder] = relationship(back_populates="items")
    part: Mapped[Part] = relationship()
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("unit_price >= 0", name="price"),
        CheckConstraint("markup_pct is null or (markup_pct >= 0 and markup_pct <= 10000)", name="markup"),
    )


class StockBatch(Base):
    """Партия товара. Себестоимость партии = цена закупки + распределённые доп. затраты."""

    __tablename__ = "stock_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    purchase_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_items.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(String(100), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    qty_received: Mapped[Decimal] = mapped_column(Qty)
    qty_remaining: Mapped[Decimal] = mapped_column(Qty)
    cost_total: Mapped[Decimal | None] = mapped_column(Money)
    cost_remaining: Mapped[Decimal | None] = mapped_column(Money)
    markup_pct: Mapped[Decimal | None] = mapped_column(Percent)

    __table_args__ = (
        CheckConstraint("qty_received > 0", name="qty_received"),
        CheckConstraint("qty_remaining >= 0", name="no_negative"),
        CheckConstraint("qty_remaining <= qty_received", name="not_above_received"),
        CheckConstraint("cost_remaining is null or cost_remaining >= 0", name="cost"),
        Index("ix_stock_batches_fifo", "part_id", "received_at", "id"),
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("stock_batches.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)  # + приход, − расход
    cost: Mapped[Decimal | None] = mapped_column(Money)  # со знаком, None — себестоимость неизвестна
    doc_type: Mapped[str] = mapped_column(String(30), default="")
    doc_id: Mapped[int | None] = mapped_column(Integer)
    line_id: Mapped[int | None] = mapped_column(Integer)
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        CheckConstraint("qty <> 0", name="nonzero"),
        Index("ix_stock_movements_doc", "doc_type", "doc_id", "line_id"),
    )


class StockReservation(Base):
    __tablename__ = "stock_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    work_order_part_id: Mapped[int] = mapped_column(ForeignKey("work_order_parts.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("status in ('active','released','consumed')", name="status"),
    )


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # write_off | surplus | opening
    reason: Mapped[str] = mapped_column(Text, default="")
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    cost: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("kind in ('write_off','surplus','opening')", name="kind"),
        CheckConstraint("qty > 0", name="qty"),
    )


class InventoryCount(Base):
    __tablename__ = "inventory_counts"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    notes: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list["InventoryCountItem"]] = relationship(cascade="all, delete-orphan", order_by="InventoryCountItem.id")
    __table_args__ = (CheckConstraint("status in ('draft','posted','cancelled')", name="status"),)


class InventoryCountItem(Base):
    __tablename__ = "inventory_count_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inventory_counts.id"), index=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    qty_expected: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    qty_actual: Mapped[Decimal | None] = mapped_column(Qty)
    qty_diff: Mapped[Decimal | None] = mapped_column(Qty)
    unit_cost_for_surplus: Mapped[Decimal | None] = mapped_column(Money)
    cost_diff: Mapped[Decimal | None] = mapped_column(Money)
    __table_args__ = (UniqueConstraint("count_id", "part_id", name="uq_inventory_count_items_part"),)


class SupplierReturn(Base):
    __tablename__ = "supplier_returns"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    total_credit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    cost_removed: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list["SupplierReturnItem"]] = relationship(cascade="all, delete-orphan")


class SupplierReturnItem(Base):
    __tablename__ = "supplier_return_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("supplier_returns.id"), index=True)
    purchase_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_items.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    credit_amount: Mapped[Decimal] = mapped_column(Money)
    cost: Mapped[Decimal | None] = mapped_column(Money)
    __table_args__ = (CheckConstraint("qty > 0", name="qty"),)


# --------------------------------------------------------------------------- продажи


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    customer_name: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    total_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_discount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_net: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_cogs: Mapped[Decimal | None] = mapped_column(Money)
    cogs_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    customer: Mapped[Customer | None] = relationship()
    items: Mapped[list["SaleItem"]] = relationship(
        back_populates="sale", order_by="SaleItem.line_no", cascade="all, delete-orphan"
    )
    __table_args__ = (CheckConstraint("status in ('draft','posted','cancelled')", name="status"),)


class SaleItem(Base):
    __tablename__ = "sale_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    cost_basis_total: Mapped[Decimal | None] = mapped_column(Money)  # себестоимость, от которой считалась цена
    markup_pct: Mapped[Decimal | None] = mapped_column(Percent)
    markup_source: Mapped[str] = mapped_column(String(20), default="")
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount_gross: Mapped[Decimal] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount_net: Mapped[Decimal] = mapped_column(Money)
    cogs: Mapped[Decimal | None] = mapped_column(Money)  # фактическая себестоимость FIFO при проведении
    cogs_known: Mapped[bool] = mapped_column(Boolean, default=True)
    returned_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))

    sale: Mapped[Sale] = relationship(back_populates="items")
    part: Mapped[Part] = relationship()
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("discount_amount >= 0 and discount_amount <= amount_gross", name="discount"),
        CheckConstraint("returned_qty >= 0 and returned_qty <= qty", name="returned"),
        CheckConstraint("markup_pct is null or (markup_pct >= 0 and markup_pct <= 10000)", name="markup"),
    )


class SaleReturn(Base):
    __tablename__ = "sale_returns"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    total_refund: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    cogs_restored: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list["SaleReturnItem"]] = relationship(cascade="all, delete-orphan")


class SaleReturnItem(Base):
    __tablename__ = "sale_return_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("sale_returns.id"), index=True)
    sale_item_id: Mapped[int] = mapped_column(ForeignKey("sale_items.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    refund_amount: Mapped[Decimal] = mapped_column(Money)
    cogs: Mapped[Decimal | None] = mapped_column(Money)
    __table_args__ = (CheckConstraint("qty > 0", name="qty"),)


# --------------------------------------------------------------------------- ремонты


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    mileage: Mapped[int | None] = mapped_column(Integer)
    complaint: Mapped[str] = mapped_column(Text, default="")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    recommendations: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    mechanic_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    services_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    parts_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    estimate_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    cogs_amount: Mapped[Decimal | None] = mapped_column(Money)
    cogs_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    vehicle: Mapped[Vehicle] = relationship()
    customer: Mapped[Customer | None] = relationship()
    mechanic: Mapped[Employee | None] = relationship()
    services: Mapped[list["WorkOrderService"]] = relationship(
        order_by="WorkOrderService.line_no", cascade="all, delete-orphan"
    )
    parts: Mapped[list["WorkOrderPart"]] = relationship(order_by="WorkOrderPart.line_no", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint(
            "status in ('new','diagnostics','awaiting_approval','awaiting_parts','in_progress','ready','issued','cancelled')",
            name="status",
        ),
    )


class WorkOrderService(Base):
    __tablename__ = "work_order_services"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), index=True)
    hours: Mapped[Decimal | None] = mapped_column(Qty)
    qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount_gross: Mapped[Decimal] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount_net: Mapped[Decimal] = mapped_column(Money)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    employee: Mapped[Employee | None] = relationship()
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("unit_price >= 0", name="price"),
        CheckConstraint("discount_amount >= 0 and discount_amount <= amount_gross", name="discount"),
    )


class WorkOrderPart(Base):
    __tablename__ = "work_order_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    is_consumable: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_basis_total: Mapped[Decimal | None] = mapped_column(Money)
    markup_pct: Mapped[Decimal | None] = mapped_column(Percent)
    markup_source: Mapped[str] = mapped_column(String(20), default="")
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount_gross: Mapped[Decimal] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount_net: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    issued_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    returned_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    cogs: Mapped[Decimal | None] = mapped_column(Money)
    cogs_known: Mapped[bool] = mapped_column(Boolean, default=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)

    part: Mapped[Part] = relationship()
    __table_args__ = (
        CheckConstraint("qty >= 0", name="qty"),
        CheckConstraint("issued_qty >= 0 and issued_qty <= qty", name="issued"),
        CheckConstraint("discount_amount >= 0 and discount_amount <= amount_gross", name="discount"),
        CheckConstraint("status in ('planned','reserved','issued','returned')", name="status"),
        CheckConstraint("markup_pct is null or (markup_pct >= 0 and markup_pct <= 10000)", name="markup"),
    )


# --------------------------------------------------------------------------- деньги


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Money)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), index=True)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), index=True)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    sale_return_id: Mapped[int | None] = mapped_column(ForeignKey("sale_returns.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("direction in ('in','out')", name="direction"),
        CheckConstraint("status in ('posted','cancelled')", name="status"),
        CheckConstraint("method in ('cash','card','transfer')", name="method"),
    )


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(20))
    payee: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("status in ('posted','cancelled')", name="status"),
    )


# --------------------------------------------------------------------------- знания и ИИ


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(30), default="document")
    source: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    make: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    engine: Mapped[str] = mapped_column(String(80), default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("part_categories.id"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    verification_status: Mapped[str] = mapped_column(String(20), default="needs_check")
    content_text: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class DiagnosticRule(Base):
    __tablename__ = "diagnostic_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    dtc_code: Mapped[str] = mapped_column(String(10), default="", index=True)
    title: Mapped[str] = mapped_column(String(300))
    keywords: Mapped[str] = mapped_column(Text, default="")
    system_code: Mapped[str] = mapped_column(String(60), default="")
    possible_causes: Mapped[list | None] = mapped_column(JSONText)
    checks: Mapped[list | None] = mapped_column(JSONText)
    part_category_codes: Mapped[list | None] = mapped_column(JSONText)
    required_info: Mapped[list | None] = mapped_column(JSONText)
    make: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(20), default="needs_check")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(30))  # diagnosis | purchase
    mode: Mapped[str] = mapped_column(String(20), default="basic")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    input: Mapped[dict | None] = mapped_column(JSONText)
    output: Mapped[dict | None] = mapped_column(JSONText)
    status: Mapped[str] = mapped_column(String(20), default="shown")


class InvoiceImport(Base):
    __tablename__ = "invoice_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="needs_review", index=True)
    engine: Mapped[str] = mapped_column(String(40), default="")
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    supplier_name: Mapped[str] = mapped_column(String(300), default="")
    supplier_inn: Mapped[str] = mapped_column(String(12), default="")
    doc_number: Mapped[str] = mapped_column(String(80), default="")
    doc_date: Mapped[date | None] = mapped_column(Date)
    total_declared: Mapped[Decimal | None] = mapped_column(Money)
    total_calculated: Mapped[Decimal | None] = mapped_column(Money)
    warnings: Mapped[list | None] = mapped_column(JSONText)
    lines: Mapped[list | None] = mapped_column(JSONText)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("status in ('needs_review','confirmed','rejected','failed','duplicate')", name="status"),
    )
