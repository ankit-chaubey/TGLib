"""
2FA (Two-Factor Authentication) SRP-2048 implementation for Telegram.
Verified against Telethon's reference implementation.
https://core.telegram.org/api/srp
"""
import hashlib
import os

SIZE_FOR_HASH = 256  # all big-number byte buffers hashed must be this size


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _sha256(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


def _pad256(b: bytes) -> bytes:
    """Left-pad bytes to exactly 256 bytes (Telegram always hashes 256-byte values)."""
    return b.rjust(SIZE_FOR_HASH, b'\x00')


def _int_to_256(n: int) -> bytes:
    return n.to_bytes(SIZE_FOR_HASH, 'big')


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ── Password hash  (matches Telethon's compute_hash exactly) ──────────────────
#
# Telegram's formula (from TDesktop source / Telethon):
#
#   SH(data, salt) = SHA256(salt || data || salt)
#
#   hash1 = SH(pw, salt1)
#   hash2 = SH(hash1, salt2)          ← double-hash before PBKDF2
#   hash3 = PBKDF2-SHA512(hash2, salt1, 100000)   ← salt = salt1
#   x     = SH(hash3, salt2)
#
# This is intentionally different from the simplified PH1/PH2 pseudocode
# sometimes quoted online — Telethon's formula is the one that actually works.

def _compute_password_hash(salt1: bytes, salt2: bytes, password: bytes) -> bytes:
    hash1 = _sha256(salt1, password, salt1)          # SH(pw, salt1)
    hash2 = _sha256(salt2, hash1, salt2)             # SH(hash1, salt2)
    hash3 = hashlib.pbkdf2_hmac('sha512', hash2, salt1, 100000)
    return _sha256(salt2, hash3, salt2)              # SH(hash3, salt2)


# ── Mod-exp range checks (prevents MITM / bad-server attacks) ─────────────────

def _is_good_mod_exp(value: int, prime: int) -> bool:
    """
    Validates that a mod-exp result is safe (at least 2048-64 = 1984 bits,
    and prime - value also has at least 1984 bits).
    """
    diff = prime - value
    min_bits = 2048 - 64
    max_bytes = 256
    if (diff < 0 or
            diff.bit_length() < min_bits or
            value.bit_length() < min_bits or
            (value.bit_length() + 7) // 8 > max_bytes):
        return False
    return True


# ── Main entry point ───────────────────────────────────────────────────────────

async def compute_srp_answer(password_obj, password: str):
    """
    Compute InputCheckPasswordSRP for auth.checkPassword.
    Raises ValueError if the password object is malformed.
    The server returns PASSWORD_HASH_INVALID if the password string is wrong.
    """
    from .tl.types import InputCheckPasswordSrp as InputCheckPasswordSRP

    if not password_obj.has_password:
        raise ValueError('This account does not have a 2FA password set.')

    algo = password_obj.current_algo
    if algo is None or not hasattr(algo, 'salt1'):
        raise ValueError(
            'Server returned an unsupported KDF algorithm: '
            + type(algo).__name__
        )

    salt1    = algo.salt1
    salt2    = algo.salt2
    g_int    = algo.g
    p_bytes  = _pad256(algo.p)            # always hash with 256-byte p
    p_int    = int.from_bytes(p_bytes, 'big')
    srp_id   = password_obj.srp_id
    g_bytes  = _int_to_256(g_int)        # g padded to 256 bytes

    # srp_B from Telegram may have leading zeros stripped — restore them
    b_bytes  = _pad256(password_obj.srp_B)
    B        = int.from_bytes(b_bytes, 'big')

    if not (0 < B < p_int):
        raise ValueError('Server sent an invalid g_b (out of range).')

    # ── Step 1: compute x (the password-derived secret) ───────────────────────
    pw_bytes = password.encode('utf-8')
    x_bytes  = _compute_password_hash(salt1, salt2, pw_bytes)
    x        = int.from_bytes(x_bytes, 'big')

    # ── Step 2: k = H(p_256 | g_256) ──────────────────────────────────────────
    k = int.from_bytes(_sha256(p_bytes, g_bytes), 'big')

    # ── Step 3: generate A = g^a mod p, retrying until A is safe ─────────────
    while True:
        a_bytes = os.urandom(256)
        a       = int.from_bytes(a_bytes, 'big')
        A       = pow(g_int, a, p_int)
        if _is_good_mod_exp(A, p_int):
            break
    a_for_hash = _int_to_256(A)

    # ── Step 4: u = H(A_256 | B_256) ──────────────────────────────────────────
    u = int.from_bytes(_sha256(a_for_hash, b_bytes), 'big')
    if u == 0:
        raise ValueError('u == 0; retry.')

    # ── Step 5: g_b = (B - k*g^x) mod p, check it's safe ────────────────────
    g_x  = pow(g_int, x, p_int)
    kg_x = (k * g_x) % p_int
    g_b  = (B - kg_x) % p_int

    if not _is_good_mod_exp(g_b, p_int):
        raise ValueError('Computed g_b failed range check; possible MITM.')

    # ── Step 6: session key K = H(g_b^(a + u*x) mod p) ───────────────────────
    S       = pow(g_b, a + u * x, p_int)
    K_bytes = _sha256(_int_to_256(S))

    # ── Step 7: M1 = H(H(p) XOR H(g) | H(s1) | H(s2) | A | B | K) ──────────
    M1 = _sha256(
        _xor(_sha256(p_bytes), _sha256(g_bytes)),
        _sha256(salt1),
        _sha256(salt2),
        a_for_hash,
        b_bytes,
        K_bytes,
    )

    return InputCheckPasswordSRP(
        srp_id=srp_id,
        A=a_for_hash,
        M1=M1,
    )
