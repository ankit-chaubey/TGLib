"""
tglib/events/newmessage.py  —  NewMessage event.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import re
from typing import Optional, Pattern, Callable, Union

from .common import EventBuilder, EventCommon, name_inner_event, _into_id_set
from ..utils import get_peer_id


@name_inner_event
class NewMessage(EventBuilder):
    """
    Fired whenever a new message arrives (text or media).

    Parameters
    ----------
    incoming : bool, optional
        If True, only incoming (not sent-by-you) messages.
    outgoing : bool, optional
        If True, only outgoing (sent-by-you) messages.
    from_users : entity or list, optional
        Filter by sender.
    forwards : bool, optional
        If True, only forwarded messages.  If False, only non-forwards.
    pattern : str | Pattern | callable, optional
        Regex or callable applied to the message text.

    Example
    -------
    ::

        @client.on(events.NewMessage(pattern=r'(?i)hello'))
        async def handler(event):
            await event.reply('Hey!')

        @client.on(events.NewMessage(outgoing=True, pattern='!ping'))
        async def pong(event):
            await event.reply('!pong')
    """

    def __init__(
        self,
        chats=None,
        *,
        blacklist_chats: bool = False,
        func: Callable = None,
        incoming: bool = None,
        outgoing: bool = None,
        from_users=None,
        forwards: bool = None,
        pattern: Union[str, Pattern, Callable] = None,
    ):
        if incoming and outgoing:
            incoming = outgoing = None
        elif incoming is not None and outgoing is None:
            outgoing = not incoming
        elif outgoing is not None and incoming is None:
            incoming = not outgoing
        elif incoming is False and outgoing is False:
            raise ValueError(
                'Setting both incoming=False and outgoing=False means '
                'no messages will ever be handled.'
            )

        super().__init__(chats, blacklist_chats=blacklist_chats, func=func)
        self.incoming   = incoming
        self.outgoing   = outgoing
        self.from_users = from_users
        self.forwards   = forwards

        if isinstance(pattern, str):
            self.pattern = re.compile(pattern).match
        elif pattern is None or callable(pattern):
            self.pattern = pattern
        elif hasattr(pattern, 'match') and callable(pattern.match):
            self.pattern = pattern.match
        else:
            raise TypeError(f'Invalid pattern type: {type(pattern).__name__}')

        self._no_check = all(x is None for x in (
            self.chats, self.incoming, self.outgoing, self.pattern,
            self.from_users, self.forwards, self.func,
        ))

    async def _resolve(self, client):
        await super()._resolve(client)
        self.from_users = await _into_id_set(client, self.from_users)

    @classmethod
    def build(cls, update, others=None, self_id=None):
        from ..tl import types
        msg = None

        if isinstance(update, (types.UpdateNewMessage,
                                types.UpdateNewChannelMessage)):
            if not isinstance(update.message, types.Message):
                return None
            msg = update.message

        elif isinstance(update, types.UpdateShortMessage):
            msg = types.Message(
                out=update.out,
                mentioned=update.mentioned,
                media_unread=update.media_unread,
                silent=update.silent,
                id=update.id,
                peer_id=types.PeerUser(update.user_id),
                from_id=types.PeerUser(self_id if update.out else update.user_id),
                message=update.message,
                date=update.date,
                fwd_from=update.fwd_from,
                via_bot_id=update.via_bot_id,
                reply_to=update.reply_to,
                entities=update.entities,
                ttl_period=update.ttl_period,
            )

        elif isinstance(update, types.UpdateShortChatMessage):
            msg = types.Message(
                out=update.out,
                mentioned=update.mentioned,
                media_unread=update.media_unread,
                silent=update.silent,
                id=update.id,
                from_id=types.PeerUser(self_id if update.out else update.from_id),
                peer_id=types.PeerChat(update.chat_id),
                message=update.message,
                date=update.date,
                fwd_from=update.fwd_from,
                via_bot_id=update.via_bot_id,
                reply_to=update.reply_to,
                entities=update.entities,
                ttl_period=update.ttl_period,
            )

        else:
            return None

        return cls.Event(msg)

    def filter(self, event):
        if self._no_check:
            return event

        msg = event.message
        if self.incoming and getattr(msg, 'out', False):
            return None
        if self.outgoing and not getattr(msg, 'out', False):
            return None

        if self.forwards is not None:
            fwd = getattr(msg, 'fwd_from', None)
            if self.forwards and not fwd:
                return None
            if not self.forwards and fwd:
                return None

        if self.from_users is not None:
            sender_id = event.sender_id
            if sender_id not in self.from_users:
                return None

        if self.pattern:
            text = getattr(msg, 'message', '') or ''
            m = self.pattern(text)
            if not m:
                return None
            event.pattern_match = m

        return super().filter(event)

    class Event(EventCommon):
        """
        Represents a new message.

        Attributes
        ----------
        message : types.Message
            The raw Telegram Message object.
        pattern_match : re.Match or None
            Populated when a regex pattern matched.
        """

        def __init__(self, message):
            self.message       = message
            self.pattern_match = None

        # ── Convenience props ──────────────────────────────────────────────

        @property
        def text(self) -> str:
            return getattr(self.message, 'message', '') or ''

        @property
        def raw_text(self) -> str:
            return self.text

        @property
        def id(self) -> int:
            return self.message.id

        @property
        def is_private(self) -> bool:
            from ..tl.types import PeerUser
            return isinstance(getattr(self.message, 'peer_id', None), PeerUser)

        @property
        def is_group(self) -> bool:
            from ..tl.types import PeerChat, PeerChannel
            peer = getattr(self.message, 'peer_id', None)
            if isinstance(peer, PeerChat):
                return True
            if isinstance(peer, PeerChannel):
                # megagroup check would need access to entity cache
                return True
            return False

        @property
        def is_channel(self) -> bool:
            from ..tl.types import PeerChannel
            return isinstance(getattr(self.message, 'peer_id', None), PeerChannel)

        @property
        def chat_id(self) -> Optional[int]:
            peer = getattr(self.message, 'peer_id', None)
            if peer is None:
                return None
            return get_peer_id(peer)

        @property
        def sender_id(self) -> Optional[int]:
            from_id = getattr(self.message, 'from_id', None)
            if from_id is not None:
                return get_peer_id(from_id)
            return self.chat_id  # in channels, sender == channel

        @property
        def out(self) -> bool:
            return bool(getattr(self.message, 'out', False))

        @property
        def reply_to_msg_id(self) -> Optional[int]:
            rt = getattr(self.message, 'reply_to', None)
            return getattr(rt, 'reply_to_msg_id', None)

        @property
        def media(self):
            return getattr(self.message, 'media', None)

        # ── Actions ────────────────────────────────────────────────────────

        async def reply(self, text: str, **kwargs):
            """Reply to this message."""
            return await self._client.send_message(
                self.chat_id, text, reply_to=self.id, **kwargs
            )

        async def respond(self, text: str, **kwargs):
            """Send a message to the same chat (without replying)."""
            return await self._client.send_message(self.chat_id, text, **kwargs)

        async def delete(self, *, revoke: bool = True):
            """Delete this message."""
            return await self._client.delete_messages(
                self.chat_id, [self.id], revoke=revoke
            )

        async def edit(self, text: str, **kwargs):
            """Edit this message (only works if it's outgoing)."""
            return await self._client.edit_message(
                self.chat_id, self.id, text, **kwargs
            )

        async def forward_to(self, entity):
            """Forward this message to another chat."""
            return await self._client.forward_messages(
                entity, [self.id], self.chat_id
            )

        async def pin(self, *, notify: bool = False):
            return await self._client.pin_message(
                self.chat_id, self.id, notify=notify
            )

        async def download_media(self, *args, **kwargs):
            return await self._client.download_media(self.message, *args, **kwargs)

        def __repr__(self) -> str:
            return (f'NewMessage.Event(id={self.id}, '
                    f'from={self.sender_id}, text={self.text[:40]!r})')
