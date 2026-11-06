"""Локальная авторизация и управление пользователями."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from autoservice.application.context import AppContext, CurrentUser, audit, get_or_404
from autoservice.domain.enums import Role
from autoservice.domain.errors import AuthError, ValidationError
from autoservice.persistence.models import User, now
from autoservice.security.passwords import hash_password, validate_password, verify_password
from autoservice.security.permissions import Perm

MAX_FAILED = 5
LOCK_MINUTES = 5


def _to_current(u: User) -> CurrentUser:
    return CurrentUser(id=u.id, username=u.username, full_name=u.full_name, role=u.role)


def has_users(ctx: AppContext) -> bool:
    with ctx.db.read() as s:
        return bool(s.scalar(select(func.count()).select_from(User)))


def _validate_username(username: str) -> str:
    username = (username or "").strip()
    if len(username) < 3:
        raise ValidationError("Имя пользователя должно содержать не менее 3 символов")
    return username


def create_first_admin(ctx: AppContext, username: str, password: str, full_name: str = "") -> CurrentUser:
    """Первый запуск: создаётся администратор. Пароль задаёт сам пользователь."""
    username = _validate_username(username)
    validate_password(password)
    with ctx.db.transaction() as s:
        if s.scalar(select(func.count()).select_from(User)):
            raise ValidationError("Администратор уже создан")
        user = User(username=username, full_name=full_name.strip(), role=Role.ADMIN, password_hash=hash_password(password))
        s.add(user)
        s.flush()
        cu = _to_current(user)
        audit(s, ctx.as_user(cu), "user.create_first_admin", "user", user.id)
        return cu


def login(ctx: AppContext, username: str, password: str) -> CurrentUser:
    # Ошибку выдаём после фиксации транзакции, чтобы счётчик неудачных попыток
    # и запись в журнале сохранились.
    error: str | None = None
    cu: CurrentUser | None = None
    with ctx.db.transaction() as s:
        user = s.execute(select(User).where(func.lower(User.username) == (username or "").strip().lower())).scalar_one_or_none()
        if user is None or not user.is_active:
            audit(s, ctx, "auth.failed", "user", None, username=username)
            error = "Неверное имя пользователя или пароль"
        elif user.locked_until and user.locked_until > now():
            error = "Слишком много неудачных попыток. Вход временно заблокирован на несколько минут."
        elif not verify_password(password or "", user.password_hash):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED:
                user.locked_until = now() + timedelta(minutes=LOCK_MINUTES)
                user.failed_attempts = 0
            audit(s, ctx, "auth.failed", "user", user.id, username=username)
            error = "Неверное имя пользователя или пароль"
        else:
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = now()
            cu = _to_current(user)
            audit(s, ctx.as_user(cu), "auth.login", "user", user.id)
    if error or cu is None:
        raise AuthError(error or "Ошибка входа")
    return cu


def verify_current_password(ctx: AppContext, password: str) -> bool:
    if ctx.user is None:
        return False
    with ctx.db.read() as s:
        user = s.get(User, ctx.user.id)
        return bool(user and user.is_active and verify_password(password or "", user.password_hash))


def list_users(ctx: AppContext) -> list[dict]:
    ctx.require(Perm.USERS_MANAGE)
    with ctx.db.read() as s:
        return [
            {
                "id": u.id,
                "username": u.username,
                "full_name": u.full_name,
                "role": u.role,
                "role_title": Role.TITLES.get(u.role, u.role),
                "is_active": u.is_active,
                "last_login_at": u.last_login_at,
            }
            for u in s.execute(select(User).order_by(User.username)).scalars()
        ]


def create_user(ctx: AppContext, username: str, password: str, role: str, full_name: str = "") -> int:
    ctx.require(Perm.USERS_MANAGE)
    username = _validate_username(username)
    validate_password(password)
    if role not in Role.TITLES:
        raise ValidationError("Неизвестная роль")
    with ctx.db.transaction() as s:
        if s.execute(select(User).where(func.lower(User.username) == username.lower())).first():
            raise ValidationError("Пользователь с таким именем уже существует")
        user = User(username=username, full_name=full_name.strip(), role=role, password_hash=hash_password(password))
        s.add(user)
        s.flush()
        audit(s, ctx, "user.create", "user", user.id, role=role)
        return user.id


def set_user_active(ctx: AppContext, user_id: int, active: bool) -> None:
    ctx.require(Perm.USERS_MANAGE)
    with ctx.db.transaction() as s:
        user = get_or_404(s, User, user_id, "Пользователь")
        if not active and user.role == Role.ADMIN:
            admins = s.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN, User.is_active.is_(True)))
            if admins <= 1:
                raise ValidationError("Нельзя отключить последнего администратора")
        user.is_active = active
        audit(s, ctx, "user.activate" if active else "user.deactivate", "user", user_id)


def change_password(ctx: AppContext, user_id: int, new_password: str, old_password: str | None = None) -> None:
    validate_password(new_password)
    with ctx.db.transaction() as s:
        user = get_or_404(s, User, user_id, "Пользователь")
        own = ctx.user and ctx.user.id == user_id
        if own:
            if old_password is None or not verify_password(old_password, user.password_hash):
                raise AuthError("Текущий пароль указан неверно")
        else:
            ctx.require(Perm.USERS_MANAGE)
        user.password_hash = hash_password(new_password)
        audit(s, ctx, "user.password_change", "user", user_id)
