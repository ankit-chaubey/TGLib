"""
tglib.events  —  High-level event builder / filter system.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.

Quick reference
---------------
events.NewMessage       — incoming / outgoing text & media messages
events.MessageEdited    — edited messages
events.MessageDeleted   — deleted messages
events.CallbackQuery    — inline button presses (bots)
events.ChatAction       — joins, leaves, title changes, …
events.Raw              — every raw update, optionally filtered by type

All builders support:
  chats             — whitelist of chats (entity / int / list)
  blacklist_chats   — invert the whitelist
  func              — arbitrary sync filter ``(event) -> bool``

Example
-------
::

    from tglib import TelegramClient
    from tglib import events

    client = TelegramClient('session', api_id, api_hash)

    @client.on(events.NewMessage(pattern=r'(?i)^hello'))
    async def greet(event):
        await event.reply('Hello there!')

    @client.on(events.CallbackQuery(data=b'ok'))
    async def on_ok(event):
        await event.answer('Done!', alert=True)
"""

from .newmessage     import NewMessage
from .messageedited  import MessageEdited
from .messagedeleted import MessageDeleted
from .callbackquery  import CallbackQuery
from .chataction     import ChatAction
from .raw            import Raw
from .common         import EventBuilder, EventCommon

__all__ = [
    'NewMessage',
    'MessageEdited',
    'MessageDeleted',
    'CallbackQuery',
    'ChatAction',
    'Raw',
    'EventBuilder',
    'EventCommon',
]

# ── Event builder registry used by the dispatcher ─────────────────────────────
# Order matters: more specific builders should come first.
ALL_EVENTS = [
    NewMessage,
    MessageEdited,
    MessageDeleted,
    CallbackQuery,
    ChatAction,
    Raw,
]
