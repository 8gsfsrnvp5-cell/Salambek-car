"""Хранение пароля шифрования для копий по расписанию через Windows DPAPI.

Пароль шифруется средствами Windows для текущей учётной записи и может быть
расшифрован только на этом компьютере под этим пользователем. Для
восстановления на другом компьютере администратор вводит пароль вручную.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

from autoservice.domain.errors import DomainError


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return os.name == "nt"


def _call(fn_name: str, data: bytes) -> bytes:
    if not available():
        raise DomainError("Защищённое хранение пароля доступно только в Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = getattr(crypt32, fn_name)
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise DomainError("Windows не смогла обработать защищённые данные (DPAPI)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(secret: str) -> str:
    return base64.b64encode(_call("CryptProtectData", secret.encode("utf-8"))).decode("ascii")


def unprotect(token: str) -> str:
    return _call("CryptUnprotectData", base64.b64decode(token)).decode("utf-8")
