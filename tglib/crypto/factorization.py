"""
Fast PQ Factorization using Pollard's Rho (Brent variant).
Required for the MTProto auth key generation step.
"""
from random import randint


class Factorization:
    """Factorizes Telegram's PQ value into primes P and Q."""

    @classmethod
    def factorize(cls, pq: int):
        """
        Factorize pq into two prime factors (p, q) with p < q.
        Uses Pollard's Rho / Brent algorithm.
        """
        if pq % 2 == 0:
            return 2, pq // 2

        y = randint(1, pq - 1)
        c = randint(1, pq - 1)
        m = randint(1, pq - 1)
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
                g = cls.gcd(q, pq)
                k += m

            r *= 2

        if g == pq:
            while True:
                ys = (pow(ys, 2, pq) + c) % pq
                g = cls.gcd(abs(x - ys), pq)
                if g > 1:
                    break

        p, q = g, pq // g
        return (p, q) if p < q else (q, p)

    @staticmethod
    def gcd(a: int, b: int) -> int:
        while b:
            a, b = b, a % b
        return a
