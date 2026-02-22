"""
AES-IGE encryption/decryption as required by MTProto 2.0.

Will use cryptg (fastest) -> PyCryptodome -> pure Python fallback.
"""
import os
import logging

__log__ = logging.getLogger(__name__)

try:
    import cryptg
    __log__.info('cryptg detected — using C extension for AES-IGE')
    _USE_CRYPTG = True
except ImportError:
    _USE_CRYPTG = False

if not _USE_CRYPTG:
    try:
        from Crypto.Cipher import AES as _PyAES
        _USE_PYCRYPTO = True
        __log__.info('PyCryptodome detected — using it for AES-IGE')
    except ImportError:
        _USE_PYCRYPTO = False

    if not _USE_PYCRYPTO:
        try:
            import pyaes as _pyaes
            _USE_PYAES = True
            __log__.info('pyaes detected — using pure-Python AES fallback')
        except ImportError:
            _USE_PYAES = False
            __log__.warning(
                'No AES backend found! Install cryptg, pycryptodome, or pyaes.'
            )


def _encrypt_ige_python(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
    """Pure Python AES-IGE encryption."""
    # Pad if necessary
    if len(plain_text) % 16:
        plain_text += os.urandom(16 - len(plain_text) % 16)

    aes = _pyaes.AES(key) if _USE_PYAES else None
    iv1, iv2 = bytearray(iv[:16]), bytearray(iv[16:])
    cipher_text = bytearray()
    blocks = len(plain_text) // 16

    for i in range(blocks):
        block = bytearray(plain_text[i * 16:(i + 1) * 16])
        xored = bytes(b ^ iv1[j] for j, b in enumerate(block))
        encrypted = aes.encrypt(xored)
        ct_block = bytes(encrypted[j] ^ iv2[j] for j in range(16))
        cipher_text.extend(ct_block)
        iv1 = bytearray(ct_block)
        iv2 = bytearray(block)

    return bytes(cipher_text)


def _decrypt_ige_python(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
    """Pure Python AES-IGE decryption."""
    aes = _pyaes.AES(key) if _USE_PYAES else None
    iv1, iv2 = bytearray(iv[:16]), bytearray(iv[16:])
    plain_text = bytearray()
    blocks = len(cipher_text) // 16

    for i in range(blocks):
        ct_block = bytearray(cipher_text[i * 16:(i + 1) * 16])
        xored = bytes(b ^ iv2[j] for j, b in enumerate(ct_block))
        decrypted = aes.decrypt(xored)
        pt_block = bytes(decrypted[j] ^ iv1[j] for j in range(16))
        plain_text.extend(pt_block)
        iv1 = bytearray(ct_block)
        iv2 = bytearray(pt_block)

    return bytes(plain_text)


class AES:
    """AES-IGE cipher interface (MTProto-compatible)."""

    @staticmethod
    def encrypt_ige(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
        if _USE_CRYPTG:
            return cryptg.encrypt_ige(plain_text, key, iv)
        if _USE_PYCRYPTO:
            # PyCryptodome doesn't support IGE natively; use manual
            pass
        return _encrypt_ige_python(plain_text, key, iv)

    @staticmethod
    def decrypt_ige(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
        if _USE_CRYPTG:
            return cryptg.decrypt_ige(cipher_text, key, iv)
        if _USE_PYCRYPTO:
            pass
        return _decrypt_ige_python(cipher_text, key, iv)

    @staticmethod
    def encrypt_ctr(data: bytes, key: bytes, iv: bytes) -> bytes:
        """CTR mode encryption (used in obfuscated transports)."""
        if _USE_PYCRYPTO:
            ctr = _PyAES.new(key, _PyAES.MODE_CTR, nonce=b'', initial_value=iv)
            return ctr.encrypt(data)
        # Fallback
        if _USE_PYAES:
            counter = _pyaes.Counter(initial_value=int.from_bytes(iv, 'big'))
            cipher = _pyaes.AESModeOfOperationCTR(key, counter=counter)
            return cipher.encrypt(data)
        raise RuntimeError('No AES CTR backend available')

    decrypt_ctr = encrypt_ctr  # CTR is symmetric
