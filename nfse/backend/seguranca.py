"""
seguranca.py — Guarda a senha do certificado de forma protegida.

- No Windows: usa o DPAPI (CryptProtectData), que cifra a senha atrelada à
  conta de usuário do Windows. Outro usuário ou outra máquina não conseguem
  decifrar.
- Em outros sistemas: usa Fernet (cryptography) com uma chave local em
  `dados/.chave`.

O valor guardado é uma string com prefixo indicando o método ("dpapi:" ou
"fernet:"), para podermos decifrar corretamente depois.
"""

from __future__ import annotations

import base64
import platform
from pathlib import Path


# ── Windows DPAPI ────────────────────────────────────────────────────────────
def _win_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData falhou")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _win_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData falhou")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ── Fernet (fallback não-Windows) ────────────────────────────────────────────
def _fernet(dados_dir: Path):
    from cryptography.fernet import Fernet

    chave = Path(dados_dir) / ".chave"
    if chave.exists():
        key = chave.read_bytes()
    else:
        key = Fernet.generate_key()
        chave.write_bytes(key)
    return Fernet(key)


# ── API pública ──────────────────────────────────────────────────────────────
def proteger(texto: str, dados_dir: Path) -> str:
    raw = texto.encode("utf-8")
    if platform.system() == "Windows":
        return "dpapi:" + base64.b64encode(_win_protect(raw)).decode("ascii")
    return "fernet:" + _fernet(dados_dir).encrypt(raw).decode("ascii")


def desproteger(blob: str, dados_dir: Path) -> str:
    if not blob:
        return ""
    if blob.startswith("dpapi:"):
        return _win_unprotect(base64.b64decode(blob[6:])).decode("utf-8")
    if blob.startswith("fernet:"):
        return _fernet(dados_dir).decrypt(blob[7:].encode("ascii")).decode("utf-8")
    return blob  # compatibilidade: valor antigo em texto puro
