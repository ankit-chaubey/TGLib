"""
AES-IGE / CTR / CBC encryption as required by MTProto 2.0.

Backend is selected by tglib.crypto.backend (see that module for details).
Priority: cipheron → cryptogram → cryptg → pycryptodome → pyaes

To override at runtime:
    from tglib.crypto.backend import set_backend
    set_backend('cryptogram')   # before connecting
"""
from __future__ import annotations

import os
import logging

from tglib.crypto import backend as _backend_mod

__log__ = logging.getLogger(__name__)


# ── Lazy imports for software-only backends ───────────────────────────────────

def _get_cryptg():
    try:
        import cryptg as _cg
        return _cg
    except ImportError:
        return None


def _get_pycrypto_aes():
    try:
        from Crypto.Cipher import AES as _a
        return _a
    except ImportError:
        return None


def _get_pyaes():
    try:
        import pyaes as _p
        return _p
    except ImportError:
        return None


# ── Pure-Python IGE (last resort) ────────────────────────────────────────────

def _encrypt_ige_python(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
    pyaes = _get_pyaes()
    if pyaes is None:
        raise RuntimeError('No AES-IGE backend available. Install cipheron or cryptogram.')
    if len(plain_text) % 16:
        plain_text += os.urandom(16 - len(plain_text) % 16)
    aes = pyaes.AES(key)
    iv1, iv2 = bytearray(iv[:16]), bytearray(iv[16:])
    ct = bytearray()
    for i in range(len(plain_text) // 16):
        blk = bytearray(plain_text[i*16:(i+1)*16])
        xrd = bytes(b ^ iv1[j] for j, b in enumerate(blk))
        enc = aes.encrypt(xrd)
        cb  = bytes(enc[j] ^ iv2[j] for j in range(16))
        ct.extend(cb)
        iv1, iv2 = bytearray(cb), bytearray(blk)
    return bytes(ct)


def _decrypt_ige_python(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
    pyaes = _get_pyaes()
    if pyaes is None:
        raise RuntimeError('No AES-IGE backend available. Install cipheron or cryptogram.')
    aes = pyaes.AES(key)
    iv1, iv2 = bytearray(iv[:16]), bytearray(iv[16:])
    pt = bytearray()
    for i in range(len(cipher_text) // 16):
        cb  = bytearray(cipher_text[i*16:(i+1)*16])
        xrd = bytes(b ^ iv2[j] for j, b in enumerate(cb))
        dec = aes.decrypt(xrd)
        pb  = bytes(dec[j] ^ iv1[j] for j in range(16))
        pt.extend(pb)
        iv1, iv2 = bytearray(cb), bytearray(pb)
    return bytes(pt)


# ── Public AES interface ──────────────────────────────────────────────────────

class AES:
    """
    AES-IGE / CTR / CBC cipher (MTProto-compatible).

    All methods dynamically dispatch to the active backend so that a
    runtime call to ``tglib.crypto.backend.set_backend(...)`` takes
    effect immediately — no restart required.
    """

    # ── IGE ──────────────────────────────────────────────────────────────────

    @staticmethod
    def encrypt_ige(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
        mod = _backend_mod.get_aes_module()
        if mod is not None:
            return mod.encrypt_ige(plain_text, key, iv)
        cryptg = _get_cryptg()
        if cryptg is not None:
            return cryptg.encrypt_ige(plain_text, key, iv)
        return _encrypt_ige_python(plain_text, key, iv)

    @staticmethod
    def decrypt_ige(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
        mod = _backend_mod.get_aes_module()
        if mod is not None:
            return mod.decrypt_ige(cipher_text, key, iv)
        cryptg = _get_cryptg()
        if cryptg is not None:
            return cryptg.decrypt_ige(cipher_text, key, iv)
        return _decrypt_ige_python(cipher_text, key, iv)

    # ── CTR ──────────────────────────────────────────────────────────────────

    @staticmethod
    def encrypt_ctr(data: bytes, key: bytes, iv: bytes) -> bytes:
        """CTR mode (used in obfuscated transports). CTR is symmetric."""
        mod = _backend_mod.get_aes_module()
        if mod is not None and hasattr(mod, 'ctr256_encrypt'):
            return mod.ctr256_encrypt(data, key, iv)
        pyc = _get_pycrypto_aes()
        if pyc is not None:
            ctr = pyc.new(key, pyc.MODE_CTR, nonce=b'', initial_value=iv)
            return ctr.encrypt(data)
        pyaes = _get_pyaes()
        if pyaes is not None:
            ctr = pyaes.Counter(initial_value=int.from_bytes(iv, 'big'))
            return pyaes.AESModeOfOperationCTR(key, counter=ctr).encrypt(data)
        raise RuntimeError('No AES-CTR backend. Install cipheron, cryptogram, or pycryptodome.')

    decrypt_ctr = encrypt_ctr  # CTR is symmetric

    # ── CBC ──────────────────────────────────────────────────────────────────

    @staticmethod
    def encrypt_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
        mod = _backend_mod.get_aes_module()
        if mod is not None and hasattr(mod, 'cbc256_encrypt'):
            return mod.cbc256_encrypt(data, key, iv)
        pyc = _get_pycrypto_aes()
        if pyc is not None:
            return pyc.new(key, pyc.MODE_CBC, iv).encrypt(data)
        raise RuntimeError('No AES-CBC backend. Install cipheron, cryptogram, or pycryptodome.')

    @staticmethod
    def decrypt_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
        mod = _backend_mod.get_aes_module()
        if mod is not None and hasattr(mod, 'cbc256_decrypt'):
            return mod.cbc256_decrypt(data, key, iv)
        pyc = _get_pycrypto_aes()
        if pyc is not None:
            return pyc.new(key, pyc.MODE_CBC, iv).decrypt(data)
        raise RuntimeError('No AES-CBC backend. Install cipheron, cryptogram, or pycryptodome.')

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @staticmethod
    def get_backend() -> str:
        """Return the name of the currently active backend."""
        info = _backend_mod.get_backend()
        return info.get('name') or 'none'
