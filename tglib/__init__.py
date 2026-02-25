"""
TGLib  —  Production-grade Telegram MTProto library for Python.
================================================================

Built and maintained by Ankit Chaubey
  GitHub  : https://github.com/ankit-chaubey/TGLib
  Contact : ankitchaubey.dev@gmail.com
  License : MIT

Portions inspired by / ported from Telethon v1
  Copyright (C) LonamiWebs (https://github.com/LonamiWebs/Telethon) — MIT License.
All modifications and original code Copyright (C) Ankit Chaubey — MIT License.

Features
--------
- Full MTProto 2.0 (AES-IGE, auth key exchange, DH, RSA, Obfuscated TCP)
- Sessions: SQLite, In-memory, String (copy-paste anywhere)
- Entity cache with LRU eviction and TTL expiry
- Upload: chunked, parallel-part-capable, big-file support, progress callbacks
- Download: chunked, CDN-aware, DC migration, streaming via ``iter_download``
- Messaging: send / edit / delete / forward / pin / search / iter_messages
- Dialogs & participants iteration
- Events: NewMessage, MessageEdited, MessageDeleted, CallbackQuery, ChatAction, Raw
- Text formatting: HTML and Markdown parsers/unparsers (handles surrogates)
- Flood-wait auto-sleep, DC migration, transient error retry
- Supports both bots and userbots

Quick start
-----------
Userbot::

    import asyncio
    from tglib import TelegramClient, events

    client = TelegramClient('session', API_ID, API_HASH)

    @client.on(events.NewMessage(pattern='(?i)hello'))
    async def greet(event):
        await event.reply('Hello there!')

    async def main():
        await client.start(phone='+123456789')
        print(await client.get_me())
        await client.run_until_disconnected()

    asyncio.run(main())

Bot::

    import asyncio
    from tglib import TelegramClient, events

    bot = TelegramClient('bot', API_ID, API_HASH)

    @bot.on(events.NewMessage(pattern='/start'))
    async def start(event):
        await event.reply('Hello from tglib bot!')

    @bot.on(events.CallbackQuery(data=b'ok'))
    async def cb(event):
        await event.answer('Done!')

    asyncio.run(bot.start(bot_token='TOKEN:HERE'))
    asyncio.run(bot.run_until_disconnected())

String sessions::

    from tglib.sessions import StringSession

    # Save
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone='+123')
    session_string = client.session.save()   # e.g. store in env var

    # Load
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
"""

from .client      import TelegramClient
from .entitycache import EntityCache, EntityType, CachedEntity
from .sessions    import SQLiteSession, MemorySession, StringSession
from .errors      import (
    TglibError, RPCError, FloodWaitError,
    SessionPasswordNeededError, SecurityError,
)
from .crypto.backend import (
    set_backend, get_backend, list_backends, print_backends, BACKENDS,
)
from . import events, tl, helpers, utils

__version__ = '2.0.0'
__author__  = 'Ankit Chaubey'
__license__ = 'MIT'

__all__ = [
    # Core client
    'TelegramClient',

    # Cache
    'EntityCache',
    'EntityType',
    'CachedEntity',

    # Sessions
    'SQLiteSession',
    'MemorySession',
    'StringSession',

    # Errors
    'TglibError',
    'RPCError',
    'FloodWaitError',
    'SessionPasswordNeededError',
    'SecurityError',

    # Events module
    'events',

    # TL / helpers
    'tl',
    'helpers',
    'utils',

    # Crypto backend control (cipheron → cryptogram → cryptg → pycryptodome → pyaes)
    'set_backend',
    'get_backend',
    'list_backends',
    'print_backends',
    'BACKENDS',

    # Metadata
    '__version__',
    '__author__',
    '__license__',
]
