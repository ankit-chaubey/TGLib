"""
tglib/helpers.py  —  Internal helpers.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
Modifications Copyright (C) tglib contributors.
"""
import asyncio
import inspect
import io
import logging
import os
import struct
from enum import Enum
from hashlib import sha1

_log = logging.getLogger(__name__)

# ── Random helpers ─────────────────────────────────────────────────────────────

def generate_random_long(signed: bool = True) -> int:
    """Generate a random 64-bit integer."""
    return int.from_bytes(os.urandom(8), signed=signed, byteorder='little')


def generate_key_data_from_nonce(server_nonce: int, new_nonce: int):
    """AES key/IV derivation for DH exchange (MTProto spec)."""
    server_nonce_bytes = server_nonce.to_bytes(16, 'little', signed=True)
    new_nonce_bytes    = new_nonce.to_bytes(32, 'little', signed=True)
    hash1 = sha1(new_nonce_bytes + server_nonce_bytes).digest()
    hash2 = sha1(server_nonce_bytes + new_nonce_bytes).digest()
    hash3 = sha1(new_nonce_bytes + new_nonce_bytes).digest()
    key = hash1 + hash2[:12]
    iv  = hash2[12:] + hash3 + new_nonce_bytes[:4]
    return key, iv


# ── Surrogate helpers (reexported from utils for backward-compat) ──────────────

def add_surrogate(text: str) -> str:
    return ''.join(
        ''.join(chr(y) for y in struct.unpack('<HH', x.encode('utf-16le')))
        if (0x10000 <= ord(x) <= 0x10FFFF) else x
        for x in text
    )


def del_surrogate(text: str) -> str:
    return text.encode('utf-16', 'surrogatepass').decode('utf-16')


def within_surrogate(text: str, index: int) -> bool:
    return 0xDC00 <= ord(text[index]) <= 0xDFFF


def strip_text(text: str, entities: list) -> str:
    if not text:
        return text
    while text and text[-1].isspace():
        for e in entities:
            if e.offset + e.length == len(text):
                e.length -= 1
        text = text[:-1]
    while text and text[0].isspace():
        for e in entities:
            if e.offset != 0:
                e.offset -= 1
            else:
                e.length -= 1
        text = text[1:]
    return text


# ── Peer type helper ───────────────────────────────────────────────────────────

class _EntityType(Enum):
    USER    = 1
    CHAT    = 2
    CHANNEL = 3


def _entity_type(entity) -> _EntityType:
    from .tl import types
    if isinstance(entity, (types.InputPeerUser, types.InputPeerSelf)):
        return _EntityType.USER
    if isinstance(entity, types.InputPeerChat):
        return _EntityType.CHAT
    if isinstance(entity, types.InputPeerChannel):
        return _EntityType.CHANNEL
    raise TypeError(f'Unknown entity type: {type(entity).__name__}')


# ── File helpers ───────────────────────────────────────────────────────────────

def ensure_parent_dir_exists(path: str):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


class _FileStream:
    """
    Async context manager that wraps any file-like / path / bytes into a
    seekable binary stream.  Sets `.file_size` and `.name`.
    """

    def __init__(self, file, file_size=None):
        self._file      = file
        self.file_size  = file_size
        self.name       = None
        self._stream    = None
        self._owned     = False

    async def __aenter__(self):
        if isinstance(self._file, str):
            self.name      = os.path.basename(self._file)
            self._stream   = open(self._file, 'rb')
            self._owned    = True
            if self.file_size is None:
                self.file_size = os.path.getsize(self._file)
        elif isinstance(self._file, bytes):
            self._stream  = io.BytesIO(self._file)
            self._owned   = True
            if self.file_size is None:
                self.file_size = len(self._file)
        else:
            self._stream = self._file
            self.name    = getattr(self._file, 'name', None)
            if self.name:
                self.name = os.path.basename(self.name)
            if self.file_size is None:
                # Attempt to determine size via seek
                pos = self._stream.tell()
                self._stream.seek(0, 2)
                self.file_size = self._stream.tell()
                self._stream.seek(pos)
        return self

    async def __aexit__(self, *args):
        if self._owned and self._stream:
            self._stream.close()

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


# ── Async helpers ──────────────────────────────────────────────────────────────

async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _sync_enter(self):
    import asyncio
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(self.__aenter__())


def _sync_exit(self, *args):
    import asyncio
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(self.__aexit__(*args))


async def retry_range(retries: int, *, delay: float = 1.0):
    for attempt in range(retries):
        yield attempt
        if attempt < retries - 1:
            await asyncio.sleep(delay)


# ── List helpers ───────────────────────────────────────────────────────────────

def is_list_like(obj) -> bool:
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))


def get_peer_id(peer) -> int:
    """Thin wrapper that delegates to utils."""
    from .utils import get_peer_id as _gpid
    return _gpid(peer)
