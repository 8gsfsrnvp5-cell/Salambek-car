"""Шифрование резервных копий: AES-256-GCM, ключ из пароля через scrypt.

Формат файла: заголовок «ASAIENC1» + соль(16) + префикс nonce(8), далее блоки
[флаг 1 байт][длина 4 байта][шифртекст+тег]. Последний блок помечен флагом,
поэтому обрезанный файл не расшифруется. Пароль нигде не сохраняется.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from autoservice.domain.errors import DomainError

MAGIC = b"ASAIENC1"
CHUNK = 4 * 1024 * 1024
_N, _R, _P = 2**15, 8, 1


class DecryptError(DomainError):
    pass


def derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise DomainError("Пароль шифрования не задан")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * 1024 * 1024, dklen=32)


def is_encrypted(path: Path) -> bool:
    with Path(path).open("rb") as fh:
        return fh.read(len(MAGIC)) == MAGIC


def encrypt_file(src: Path, dest: Path, password: str) -> None:
    salt = os.urandom(16)
    prefix = os.urandom(8)
    header = MAGIC + salt + prefix
    aes = AESGCM(derive_key(password, salt))
    with Path(src).open("rb") as fin, Path(dest).open("wb") as fout:
        fout.write(header)
        counter = 0
        chunk = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            final = 1 if not nxt else 0
            nonce = prefix + struct.pack(">I", counter)
            aad = header + struct.pack(">IB", counter, final)
            ct = aes.encrypt(nonce, chunk, aad)
            fout.write(struct.pack(">BI", final, len(ct)))
            fout.write(ct)
            if final:
                break
            chunk = nxt
            counter += 1


def decrypt_file(src: Path, dest: Path, password: str) -> None:
    with Path(src).open("rb") as fin, Path(dest).open("wb") as fout:
        header = fin.read(len(MAGIC) + 24)
        if not header.startswith(MAGIC) or len(header) != len(MAGIC) + 24:
            raise DecryptError("Файл не является зашифрованной копией AutoService AI")
        salt = header[len(MAGIC):len(MAGIC) + 16]
        prefix = header[len(MAGIC) + 16:]
        aes = AESGCM(derive_key(password, salt))
        counter = 0
        while True:
            meta = fin.read(5)
            if len(meta) != 5:
                raise DecryptError("Зашифрованная копия обрезана или повреждена")
            final, length = struct.unpack(">BI", meta)
            ct = fin.read(length)
            if len(ct) != length:
                raise DecryptError("Зашифрованная копия обрезана или повреждена")
            nonce = prefix + struct.pack(">I", counter)
            aad = header + struct.pack(">IB", counter, final)
            try:
                fout.write(aes.decrypt(nonce, ct, aad))
            except InvalidTag as exc:
                raise DecryptError("Неверный пароль или файл копии повреждён") from exc
            if final:
                if fin.read(1):
                    raise DecryptError("Лишние данные после конца копии — файл повреждён")
                return
            counter += 1
