"""
Fast PQ factorization for the MTProto auth-key generation step.

Backend is selected by tglib.crypto.backend.
Priority: cipheron → cryptogram → pure Python (Pollard's Rho / Brent)

To override:
    from tglib.crypto.backend import set_backend
    set_backend('cryptogram')
"""
from __future__ import annotations

import logging
from random import randint as _randint

from tglib.crypto import backend as _backend_mod

__log__ = logging.getLogger(__name__)


# ── Pure-Python Pollard's Rho (Brent variant) — always available ─────────────

def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _factorize_python(pq: int):
    if pq % 2 == 0:
        return 2, pq // 2
    y = _randint(1, pq - 1)
    c = _randint(1, pq - 1)
    m = _randint(1, pq - 1)
    g = r = q = 1
    x = ys = 0
    while g == 1:
        x = y
        for _ in range(r):
            y = (pow(y, 2, pq) + c) % pq
        k = 0
        while k < r and g == 1:
            ys = y
            for _ in range(min(m, r - k)):
                y = (pow(y, 2, pq) + c) % pq
                q = q * abs(x - y) % pq
            g = _gcd(q, pq)
            k += m
        r *= 2
    if g == pq:
        while True:
            ys = (pow(ys, 2, pq) + c) % pq
            g = _gcd(abs(x - ys), pq)
            if g > 1:
                break
    p, fq = g, pq // g
    return (p, fq) if p < fq else (fq, p)


# ── Public interface ──────────────────────────────────────────────────────────

class Factorization:
    """
    Factorizes Telegram's PQ value into primes (p, q) with p < q.

    Dynamically dispatches to the active backend's C implementation when
    available (cipheron or cryptogram), falling back to pure Python.
    """

    @classmethod
    def factorize(cls, pq: int):
        fn = _backend_mod.get_factorize_fn()
        if fn is not None:
            return fn(pq)
        return _factorize_python(pq)

    @staticmethod
    def get_backend() -> str:
        info = _backend_mod.get_backend()
        name = info.get('name')
        if name and 'factorize' in info.get('supports', set()):
            return name
        return "Python/Pollard's Rho"

    @staticmethod
    def gcd(a: int, b: int) -> int:
        return _gcd(a, b)
