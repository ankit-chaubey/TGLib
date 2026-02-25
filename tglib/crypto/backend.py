"""
tglib.crypto.backend
====================
Central registry for TGLib's pluggable crypto backend.

Priority (auto-selected unless overridden):
  1. cipheron   — ARM-CE / AES-NI via OpenSSL EVP  ← default / fastest
  2. cryptogram — AES-NI C ext with pure-Python fallback
  3. cryptg     — legacy C extension
  4. pycryptodome — software (CTR/CBC only; IGE via Python)
  5. pyaes      — pure-Python (always available as last resort)

Runtime override
----------------
  # via environment variable (set before process starts):
  TGLIB_CRYPTO_BACKEND=cryptogram python my_app.py

  # via Python, before the client is created:
  from tglib.crypto.backend import set_backend
  set_backend('cryptogram')

Inspect
-------
  from tglib.crypto.backend import get_backend, list_backends
  print(get_backend())       # {'name': 'cipheron', 'hw_accel': True, ...}
  print(list_backends())     # full table of available / unavailable backends
"""
from __future__ import annotations

import os
import sys
import logging
import importlib
from typing import Any

__all__ = [
    'set_backend',
    'get_backend',
    'list_backends',
    'get_aes_module',
    'get_factorize_fn',
    'BACKENDS',
]

__log__ = logging.getLogger(__name__)

# ── Ordered list of recognised backend names ─────────────────────────────────
BACKENDS: list[str] = ['cipheron', 'cryptogram', 'cryptg', 'pycryptodome', 'pyaes']

# ── Internal state ────────────────────────────────────────────────────────────
_registry:  dict[str, dict[str, Any]] = {}  # name → probe result
_active:    str | None = None               # currently selected backend name


# ── Probe helpers ─────────────────────────────────────────────────────────────

def _probe_cipheron() -> dict | None:
    try:
        mod = importlib.import_module('cipheron')
        return {
            'module':    mod,
            'hw_accel':  mod.has_aesni(),
            'hw_detail': mod.get_backend(),
            'supports':  {'ige', 'ctr', 'cbc', 'factorize'},
        }
    except ImportError:
        return None


def _probe_cryptogram() -> dict | None:
    try:
        mod = importlib.import_module('cryptogram')
        return {
            'module':    mod,
            'hw_accel':  mod.has_aesni(),
            'hw_detail': mod.get_backend(),
            'supports':  {'ige', 'ctr', 'cbc', 'factorize'},
        }
    except ImportError:
        return None


def _probe_cryptg() -> dict | None:
    try:
        mod = importlib.import_module('cryptg')
        return {
            'module':    mod,
            'hw_accel':  True,   # cryptg is always C / AES-NI when present
            'hw_detail': 'C/AES-NI',
            'supports':  {'ige'},  # cryptg has no CTR/CBC/factorize
        }
    except ImportError:
        return None


def _probe_pycryptodome() -> dict | None:
    try:
        from Crypto.Cipher import AES as _aes  # noqa: F401
        return {
            'module':    None,   # imported on-demand inside aes.py
            'hw_accel':  False,
            'hw_detail': 'software',
            'supports':  {'ctr', 'cbc'},  # no IGE, no factorize
        }
    except ImportError:
        return None


def _probe_pyaes() -> dict | None:
    try:
        mod = importlib.import_module('pyaes')
        return {
            'module':    mod,
            'hw_accel':  False,
            'hw_detail': 'pure-Python',
            'supports':  {'ige', 'ctr'},
        }
    except ImportError:
        return None


_PROBES = {
    'cipheron':    _probe_cipheron,
    'cryptogram':  _probe_cryptogram,
    'cryptg':      _probe_cryptg,
    'pycryptodome':_probe_pycryptodome,
    'pyaes':       _probe_pyaes,
}


# ── Build registry at import time ─────────────────────────────────────────────

def _build_registry() -> None:
    for name, probe in _PROBES.items():
        result = probe()
        _registry[name] = result  # None → not installed


_build_registry()


# ── Auto-select active backend ────────────────────────────────────────────────

def _auto_select() -> str | None:
    for name in BACKENDS:
        if _registry.get(name) is not None:
            return name
    return None


def _apply_env_override() -> None:
    global _active
    env = os.environ.get('TGLIB_CRYPTO_BACKEND', '').strip().lower()
    if env:
        if env not in BACKENDS:
            __log__.warning(
                'TGLIB_CRYPTO_BACKEND=%r is not a recognised backend %s — ignoring.',
                env, BACKENDS,
            )
        elif _registry.get(env) is None:
            __log__.warning(
                'TGLIB_CRYPTO_BACKEND=%r is not installed — falling back to auto-select.',
                env,
            )
        else:
            __log__.info('Backend forced via env: %s', env)
            _active = env
            return
    _active = _auto_select()


_apply_env_override()

if _active:
    info = _registry[_active]
    __log__.info(
        'TGLib crypto backend: %s  |  hw_accel=%s  |  %s',
        _active, info['hw_accel'], info['hw_detail'],
    )
else:
    __log__.critical(
        'No crypto backend found! Install at least one of: %s', BACKENDS
    )


# ── Public API ────────────────────────────────────────────────────────────────

def set_backend(name: str) -> None:
    """
    Switch the active crypto backend at runtime.

    Must be called *before* the TGLib client is connected.
    ``name`` must be one of: 'cipheron', 'cryptogram', 'cryptg',
    'pycryptodome', 'pyaes'.

    Raises
    ------
    ValueError
        If ``name`` is unknown or not currently installed.

    Example
    -------
    >>> from tglib.crypto.backend import set_backend
    >>> set_backend('cryptogram')
    """
    global _active
    name = name.lower().strip()
    if name not in BACKENDS:
        raise ValueError(
            f'{name!r} is not a valid backend. Choose from: {BACKENDS}'
        )
    if _registry.get(name) is None:
        installed = [k for k, v in _registry.items() if v is not None]
        raise ValueError(
            f'{name!r} is not installed. '
            f'Installed backends: {installed or ["none"]}'
        )
    _active = name
    info = _registry[_active]
    __log__.info(
        'Switched crypto backend → %s  |  hw_accel=%s  |  %s',
        _active, info['hw_accel'], info['hw_detail'],
    )


def get_backend() -> dict:
    """
    Return a dict describing the currently active backend.

    Returns
    -------
    dict with keys:
      name       – backend name (str)
      hw_accel   – True if hardware AES acceleration is active (bool)
      hw_detail  – human-readable description of the acceleration (str)
      supports   – set of operation families: 'ige', 'ctr', 'cbc', 'factorize'
      installed  – always True when returned from this function

    Example
    -------
    >>> from tglib.crypto.backend import get_backend
    >>> info = get_backend()
    >>> print(f"Using {info['name']} — HW accel: {info['hw_accel']}")
    Using cipheron — HW accel: True
    """
    if _active is None:
        return {'name': None, 'hw_accel': False, 'hw_detail': 'none', 'supports': set(), 'installed': False}
    info = dict(_registry[_active])
    info['name']      = _active
    info['installed'] = True
    info.pop('module', None)   # don't expose raw module object
    return info


def list_backends() -> list[dict]:
    """
    Return a list of all recognised backends with their availability status.

    Example
    -------
    >>> from tglib.crypto.backend import list_backends
    >>> for b in list_backends():
    ...     status = '✅ active' if b['active'] else ('✔ installed' if b['installed'] else '✗ missing')
    ...     print(f"  {b['name']:15s} {status}")
    """
    result = []
    for name in BACKENDS:
        info = _registry.get(name)
        row: dict = {'name': name, 'installed': info is not None, 'active': name == _active}
        if info:
            row['hw_accel']  = info['hw_accel']
            row['hw_detail'] = info['hw_detail']
            row['supports']  = info['supports']
        else:
            row['hw_accel']  = False
            row['hw_detail'] = 'not installed'
            row['supports']  = set()
        result.append(row)
    return result


def print_backends() -> None:
    """Pretty-print the backend table to stdout."""
    rows = list_backends()
    print('\nTGLib Crypto Backends')
    print('─' * 62)
    print(f'  {"Backend":<15}  {"Status":<14}  {"HW Accel":<10}  Supports')
    print('─' * 62)
    for r in rows:
        if r['active']:
            status = '▶ ACTIVE'
        elif r['installed']:
            status = '✔ installed'
        else:
            status = '✗ missing'
        hw   = '✔ yes' if r['hw_accel'] else '✗ no'
        caps = ', '.join(sorted(r['supports'])) or '—'
        print(f"  {r['name']:<15}  {status:<14}  {hw:<10}  {caps}")
    print('─' * 62)
    print(f'  Active: {_active or "none"}\n')


# ── Internal helpers (used by aes.py and factorization.py) ───────────────────

def get_aes_module():
    """
    Return the active backend module if it has native IGE support,
    else None (callers must use their own software path).
    """
    if _active and 'ige' in (_registry[_active] or {}).get('supports', set()):
        return _registry[_active]['module']
    return None


def get_factorize_fn():
    """
    Return the active backend's factorize_pq_pair callable,
    or None if the backend doesn't support it.
    """
    if _active and 'factorize' in (_registry[_active] or {}).get('supports', set()):
        mod = _registry[_active]['module']
        fn  = getattr(mod, 'factorize_pq_pair', None)
        return fn
    return None
