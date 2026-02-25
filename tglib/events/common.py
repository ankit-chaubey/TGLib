"""
tglib/events/common.py  —  Base classes for the event system.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import abc
import asyncio
from typing import Optional, Set


def name_inner_event(cls):
    """
    Class decorator that creates a nested ``Event`` class exposing
    ``cls.Event`` and sets ``__name__`` on it.
    """
    if not hasattr(cls, 'Event'):
        raise AttributeError(f'{cls.__name__} must define an inner Event class')
    cls.Event.__name__ = f'{cls.__name__}.Event'
    cls.Event.__qualname__ = f'{cls.__qualname__}.Event'
    return cls


async def _into_id_set(client, chats) -> Optional[Set[int]]:
    """Convert a chat / list of chats into a set of marked peer IDs."""
    from ..utils import get_peer_id
    from ..tl.types import PeerUser, PeerChat, PeerChannel
    from ..tl.tlobject import TLObject

    if chats is None:
        return None

    if not (hasattr(chats, '__iter__') and not isinstance(chats, (str, bytes))):
        chats = (chats,)

    result: Set[int] = set()
    for chat in chats:
        if isinstance(chat, int):
            if chat < 0:
                result.add(chat)
            else:
                result.update({
                    chat,
                    -chat,
                    int('-100' + str(chat)),
                })
        elif isinstance(chat, TLObject) and hasattr(chat, 'user_id'):
            result.add(get_peer_id(PeerUser(chat.user_id)))
        elif isinstance(chat, TLObject) and hasattr(chat, 'chat_id'):
            result.add(get_peer_id(PeerChat(chat.chat_id)))
        elif isinstance(chat, TLObject) and hasattr(chat, 'channel_id'):
            result.add(get_peer_id(PeerChannel(chat.channel_id)))
        else:
            try:
                ip = await client.get_input_entity(chat)
                from ..tl.types import InputPeerSelf
                if isinstance(ip, InputPeerSelf):
                    me = await client.get_me()
                    if me:
                        result.add(me.id)
                else:
                    result.add(get_peer_id(ip))
            except Exception:
                pass

    return result


class EventBuilder(abc.ABC):
    """
    Base class for all event builders (filters).

    Parameters
    ----------
    chats : entity or list of entities, optional
        Whitelist of chats. If None, all chats are accepted.
    blacklist_chats : bool
        If True, treat *chats* as a blacklist instead of whitelist.
    func : callable, optional
        Extra filter function ``(event) -> bool``.
    """

    def __init__(self, chats=None, *, blacklist_chats=False, func=None):
        self.chats           = chats
        self.blacklist_chats = bool(blacklist_chats)
        self.resolved        = False
        self.func            = func
        self._resolve_lock: Optional[asyncio.Lock] = None

    @classmethod
    @abc.abstractmethod
    def build(cls, update, others=None, self_id=None):
        """
        Build an Event from a raw update, or return None if not applicable.
        """

    async def resolve(self, client):
        """Resolve entity filters (run once before the first dispatch)."""
        if self.resolved:
            return
        if not self._resolve_lock:
            self._resolve_lock = asyncio.Lock()
        async with self._resolve_lock:
            if not self.resolved:
                await self._resolve(client)
                self.resolved = True

    async def _resolve(self, client):
        self.chats = await _into_id_set(client, self.chats)

    def filter(self, event: 'EventCommon') -> Optional['EventCommon']:
        """Return event if it passes all filters, else None."""
        if self.chats is not None:
            chat_id = getattr(event, 'chat_id', None)
            if chat_id is None:
                return None
            inside = chat_id in self.chats
            if self.blacklist_chats:
                if inside:
                    return None
            elif not inside:
                return None

        if self.func:
            try:
                result = self.func(event)
                if asyncio.iscoroutine(result):
                    # Sync filter only; async funcs are not supported here
                    import warnings
                    warnings.warn(
                        'Async filter functions are not supported in '
                        'EventBuilder.filter(); the event will be accepted.'
                    )
                elif not result:
                    return None
            except Exception:
                return None

        return event


class EventCommon:
    """
    Mixin / base for all concrete Event objects.
    Provides convenient chat_id, sender_id, and reply helpers.
    """

    _client   = None   # set by dispatcher
    _entities: dict = {}

    @property
    def client(self):
        return self._client

    @property
    def chat_id(self) -> Optional[int]:
        """Marked peer ID of the chat this event originates from."""
        return None

    @property
    def sender_id(self) -> Optional[int]:
        return None

    async def get_chat(self):
        if self.chat_id is None:
            return None
        try:
            return await self._client.get_entity(self.chat_id)
        except Exception:
            return None

    async def get_sender(self):
        if self.sender_id is None:
            return None
        try:
            return await self._client.get_entity(self.sender_id)
        except Exception:
            return None

    async def reply(self, *args, **kwargs):
        raise NotImplementedError

    async def respond(self, *args, **kwargs):
        raise NotImplementedError

    def __repr__(self) -> str:
        return f'{type(self).__name__}(chat_id={self.chat_id})'
