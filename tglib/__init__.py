"""
tglib - A full MTProto 2.0 Python library for Telegram
======================================================

Similar to Telethon and Pyrogram, tglib provides:
  - Full MTProto 2.0 implementation
  - Auth key generation (DH exchange)
  - AES-IGE encrypted transport
  - TL (Type Language) serialization/deserialization
  - High-level client API: send_message, get_me, etc.
  - Raw API access: await client(SomeRequest(...))
  - Session persistence (SQLite)
  - Event/update handling

Quick Start:
    from tglib import TelegramClient
    from tglib.tl import functions, types

    client = TelegramClient('session', api_id, api_hash)

    async def main():
        await client.connect()
        me = await client.get_me()
        await client.send_message('me', 'Hello!')
        await client.disconnect()
"""
from .client import TelegramClient
from .sessions import SQLiteSession, MemorySession
from .errors import (
    TglibError, RPCError, FloodWaitError,
    SessionPasswordNeededError, SecurityError,
)
from .crypto.backend import (
    set_backend,
    get_backend,
    list_backends,
    print_backends,
    BACKENDS,
)
from . import tl, helpers

__version__ = '1.0.0'
__all__ = [
    'TelegramClient',
    'SQLiteSession',
    'MemorySession',
    'TglibError',
    'RPCError',
    'FloodWaitError',
    'SessionPasswordNeededError',
    'SecurityError',
    'tl',
    'helpers',
    # Crypto backend control
    'set_backend',
    'get_backend',
    'list_backends',
    'print_backends',
    'BACKENDS',
]
