"""Хеширование паролей: scrypt с индивидуальной солью (стандартная библиотека)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from autoservice.domain.errors import ValidationError

_N, _R, _P = 2**15, 8, 1
_DKLEN = 32
_MAXMEM = 128 * 1024 * 1024
MIN_PASSWORD_LENGTH = 8


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов")
    if password.strip() != password:
        raise ValidationError("Пароль не должен начинаться или заканчиваться пробелом")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(bytes.fromhex(hash_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)
