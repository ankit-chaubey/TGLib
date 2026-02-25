"""
AES-IGE / CTR / CBC encryption as required by MTProto 2.0.

Backend is selected by tglib.crypto.backend (see that module for details).
Priority: cipheron → cryptogram → cryptg → pycryptodome → pyaes

To override at runtime:
    from tglib.crypto.backend import set_backend
    set_backend('cryptogram')   # before connecting

Fixes applied vs. original:
  1. _pad16() — plaintext is padded to a 16-byte boundary with random bytes
     *before* any backend sees it.  Without this every C/Rust backend raised:
         ValueError: data size must be a multiple of 16 bytes
     The pure-Python path used to pad internally; now padding is centralised
     here so ALL backends behave identically.

  2. ctr256_encrypt() call fixed — cipheron/cryptogram require the signature:
         ctr256_encrypt(data, key, iv_bytearray, state_bytearray)
     The old code passed only three args, which crashed with a TypeError.

  3. PyCryptodome IGE — the old code fell through with `pass` and never
     actually used PyCryptodome for IGE.  Now it uses a proper ECB-based
     manual IGE implementation as a genuine fallback.
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


# ── Padding helper — THE core bug-fix ────────────────────────────────────────

def _pad16(data: bytes) -> bytes:
    """
    Pad *data* to a 16-byte boundary with random bytes.

    MTProto explicitly allows random padding for client_dh_inner_data
    (step 3 of the DH handshake).  The SHA-1 hash covers only the
    inner content so the receiver ignores trailing padding bytes.

    Without this, sha1(client_dh_inner) + client_dh_inner is almost
    always NOT a multiple of 16, causing every C/Rust AES-IGE backend
    to raise ValueError.
    """
    rem = len(data) % 16
    if rem:
        data += os.urandom(16 - rem)
    return data


# ── Pure-Python IGE (last resort — pyaes backend) ────────────────────────────

def _encrypt_ige_python(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
    pyaes = _get_pyaes()
    if pyaes is None:
        raise RuntimeError('No AES-IGE backend available. Install cipheron, cryptogram, cryptg, or pyaes.')
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
        raise RuntimeError('No AES-IGE backend available. Install cipheron, cryptogram, cryptg, or pyaes.')
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


# ── PyCryptodome IGE (manual ECB block-by-block) ─────────────────────────────

def _encrypt_ige_pycryptodome(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
    _PyAES = _get_pycrypto_aes()
    iv1, iv2 = bytearray(iv[:16]), bytearray(iv[16:])
    ct = bytearray()
    for i in range(len(plain_text) // 16):
        blk = plain_text[i*16:(i+1)*16]
        xrd = bytes(a ^ b for a, b in zip(blk, iv1))
        enc = _PyAES.new(key, _PyAES.MODE_ECB).encrypt(xrd)
        cb  = bytes(a ^ b for a, b in zip(enc, iv2))
        ct += cb
        iv1, iv2 = bytearray(cb), bytearray(blk)
    return bytes(ct)


def _decrypt_ige_pycryptodome(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
    _PyAES = _get_pycrypto_aes()
    iv2, iv1 = bytearray(iv[:16]), bytearray(iv[16:])
    pt = bytearray()
    for i in range(len(cipher_text) // 16):
        cb  = cipher_text[i*16:(i+1)*16]
        xrd = bytes(a ^ b for a, b in zip(cb, iv1))
        dec = _PyAES.new(key, _PyAES.MODE_ECB).decrypt(xrd)
        pb  = bytes(a ^ b for a, b in zip(dec, iv2))
        pt += pb
        iv1, iv2 = bytearray(pb), bytearray(cb)
    return bytes(pt)


# ── Public AES interface ──────────────────────────────────────────────────────

class AES:
    """
    AES-IGE / CTR / CBC cipher (MTProto-compatible).

    All methods dynamically dispatch to the active backend so that a
    runtime call to ``tglib.crypto.backend.set_backend(...)`` takes
    effect immediately — no restart required.

    Backend fallback chain for each operation
    -----------------------------------------
    IGE encrypt/decrypt:
        cipheron → cryptogram → cryptg → pycryptodome (ECB manual) → pyaes

    CTR encrypt/decrypt:
        cipheron → cryptogram → pycryptodome (MODE_CTR) → pyaes
        (cryptg has no CTR support)

    CBC encrypt/decrypt:
        cipheron → cryptogram → pycryptodome (MODE_CBC)
    """

    # ── IGE ──────────────────────────────────────────────────────────────────

    @staticmethod
    def encrypt_ige(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
        # ★ PAD FIRST — this is the primary bug-fix.
        #   sha1(client_dh_inner) + client_dh_inner is almost never a
        #   multiple of 16, so every C/Rust backend crashed before this.
        plain_text = _pad16(plain_text)

        mod = _backend_mod.get_aes_module()   # cipheron / cryptogram
        if mod is not None:
            return mod.encrypt_ige(plain_text, key, iv)

        cg = _get_cryptg()
        if cg is not None:
            return cg.encrypt_ige(plain_text, key, iv)

        pyc = _get_pycrypto_aes()
        if pyc is not None:
            return _encrypt_ige_pycryptodome(plain_text, key, iv)

        return _encrypt_ige_python(plain_text, key, iv)

    @staticmethod
    def decrypt_ige(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
        # Ciphertext from the network is already padded by the server —
        # do NOT pad here; a mis-sized ciphertext is a genuine protocol error.
        mod = _backend_mod.get_aes_module()
        if mod is not None:
            return mod.decrypt_ige(cipher_text, key, iv)

        cg = _get_cryptg()
        if cg is not None:
            return cg.decrypt_ige(cipher_text, key, iv)

        pyc = _get_pycrypto_aes()
        if pyc is not None:
            return _decrypt_ige_pycryptodome(cipher_text, key, iv)

        return _decrypt_ige_python(cipher_text, key, iv)

    # ── CTR ──────────────────────────────────────────────────────────────────

    @staticmethod
    def encrypt_ctr(data: bytes, key: bytes, iv: bytes) -> bytes:
        """CTR mode (used in obfuscated transports). CTR is symmetric."""
        mod = _backend_mod.get_aes_module()
        if mod is not None and hasattr(mod, 'ctr256_encrypt'):
            # cipheron / cryptogram require (data, key, iv_bytearray, state_bytearray)
            # — NOT the 3-arg form used by the old buggy code.
            iv_ba   = bytearray(iv[:16])
            state   = bytearray(1)          # state byte = current keystream offset
            return mod.ctr256_encrypt(data, key, iv_ba, state)

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
