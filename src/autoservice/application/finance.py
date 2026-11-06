"""Операционные расходы и сотрудники."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from autoservice.application.context import AppContext, as_date, audit, get_or_404, next_number
from autoservice.domain.enums import PaymentMethod
from autoservice.domain.errors import ValidationError
from autoservice.domain.money import positive_money
from autoservice.persistence.models import Employee, Expense, now
from autoservice.security.permissions import Perm


def add_expense(ctx: AppContext, category: str, amount, method: str, doc_date=None, payee: str = "", description: str = "") -> int:
    ctx.require(Perm.EXPENSE_EDIT)
    category = (category or "").strip()
    if not category:
        raise ValidationError("Укажите статью расхода")
    if method not in PaymentMethod.TITLES:
        raise ValidationError("Выберите способ оплаты")
    amount = positive_money(amount, "Сумма расхода")
    with ctx.db.transaction() as s:
        e = Expense(number=next_number(s, "expense", "РС-", 6), doc_date=as_date(doc_date), category=category, amount=amount,
                    method=method, payee=(payee or "").strip(), description=(description or "").strip(), created_by=ctx.user_id)
        s.add(e)
        s.flush()
        audit(s, ctx, "expense.create", "expense", e.id, number=e.number, amount=str(amount), category=category)
        return e.id


def cancel_expense(ctx: AppContext, expense_id: int, reason: str) -> None:
    ctx.require(Perm.OPERATION_CANCEL)
    if not (reason or "").strip():
        raise ValidationError("Укажите причину отмены")
    with ctx.db.transaction() as s:
        e = get_or_404(s, Expense, expense_id, "Расход")
        if e.status != "posted":
            raise ValidationError("Расход уже отменён")
        e.status = "cancelled"
        e.cancelled_at = now()
        e.cancelled_by = ctx.user_id
        e.cancel_reason = reason.strip()
        audit(s, ctx, "expense.cancel", "expense", e.id, number=e.number, reason=reason)


def list_expenses(ctx: AppContext, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    ctx.require(Perm.FINANCE_VIEW)
    with ctx.db.read() as s:
        stmt = select(Expense).order_by(Expense.doc_date.desc(), Expense.id.desc())
        if date_from:
            stmt = stmt.where(Expense.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(Expense.doc_date <= date_to)
        return [
            {"id": e.id, "number": e.number, "doc_date": e.doc_date, "category": e.category, "amount": e.amount,
             "method": e.method, "method_title": PaymentMethod.TITLES.get(e.method, e.method), "payee": e.payee,
             "description": e.description, "status": e.status, "status_title": "Проведён" if e.status == "posted" else "Отменён",
             "cancel_reason": e.cancel_reason}
            for e in s.execute(stmt).scalars()
        ]


# --------------------------------------------------------------------------- сотрудники


def save_employee(ctx: AppContext, data: dict, employee_id: int | None = None) -> int:
    ctx.require(Perm.EMPLOYEES_EDIT)
    name = (data.get("full_name") or "").strip()
    if not name:
        raise ValidationError("Укажите ФИО сотрудника")
    with ctx.db.transaction() as s:
        if employee_id:
            emp = get_or_404(s, Employee, employee_id, "Сотрудник")
        else:
            emp = Employee(full_name=name)
            s.add(emp)
        emp.full_name = name
        emp.position = (data.get("position") or "Механик").strip()
        emp.phone = (data.get("phone") or "").strip()
        emp.notes = (data.get("notes") or "").strip()
        if "is_active" in data:
            emp.is_active = bool(data["is_active"])
        s.flush()
        audit(s, ctx, "employee.update" if employee_id else "employee.create", "employee", emp.id, name=name)
        return emp.id


def list_employees(ctx: AppContext, include_inactive: bool = False) -> list[dict]:
    with ctx.db.read() as s:
        stmt = select(Employee).order_by(Employee.full_name)
        if not include_inactive:
            stmt = stmt.where(Employee.is_active.is_(True))
        return [
            {"id": e.id, "full_name": e.full_name, "position": e.position, "phone": e.phone, "is_active": e.is_active, "notes": e.notes}
            for e in s.execute(stmt).scalars()
        ]
