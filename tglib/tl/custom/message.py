"""
tglib/tl/custom/message.py  —  Rich Message wrapper.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions adapted from Telethon v1 tl/custom/message.py
Copyright (C) LonamiWebs — MIT License.
"""
from typing import Optional


class Message:
    """
    Thin wrapper around a raw ``types.Message`` that adds convenience
    methods compatible with Telethon's custom Message API.

    The raw TL object is always accessible via ``message.raw``.
    """

    def __init__(self, raw, client=None, entities: dict = None):
        self._raw     = raw
        self._client  = client
        self._entities = entities or {}

    # ── Raw access ──────────────────────────────────────────────────────────

    @property
    def raw(self):
        """The underlying ``types.Message`` TL object."""
        return self._raw

    # ── Attribute proxy ─────────────────────────────────────────────────────

    def __getattr__(self, name):
        try:
            return getattr(self._raw, name)
        except AttributeError:
            raise AttributeError(
                f'{type(self).__name__!r} has no attribute {name!r}')

    # ── Convenience properties ───────────────────────────────────────────────

    @property
    def id(self) -> int:
        return self._raw.id

    @property
    def text(self) -> str:
        return getattr(self._raw, 'message', '') or ''

    @property
    def raw_text(self) -> str:
        return self.text

    @property
    def out(self) -> bool:
        return bool(getattr(self._raw, 'out', False))

    @property
    def mentioned(self) -> bool:
        return bool(getattr(self._raw, 'mentioned', False))

    @property
    def media(self):
        return getattr(self._raw, 'media', None)

    @property
    def has_media(self) -> bool:
        return self.media is not None

    @property
    def date(self):
        return getattr(self._raw, 'date', None)

    @property
    def edit_date(self):
        return getattr(self._raw, 'edit_date', None)

    @property
    def reply_to(self):
        return getattr(self._raw, 'reply_to', None)

    @property
    def reply_to_msg_id(self) -> Optional[int]:
        rt = self.reply_to
        return getattr(rt, 'reply_to_msg_id', None)

    @property
    def is_reply(self) -> bool:
        return self.reply_to_msg_id is not None

    @property
    def forward(self):
        return getattr(self._raw, 'fwd_from', None)

    @property
    def is_forward(self) -> bool:
        return self.forward is not None

    @property
    def buttons(self):
        markup = getattr(self._raw, 'reply_markup', None)
        if markup is None:
            return None
        rows = getattr(markup, 'rows', None)
        if rows is None:
            return None
        return [[btn for btn in row.buttons] for row in rows]

    @property
    def file(self):
        return self.media

    @property
    def photo(self):
        from ..types import MessageMediaPhoto, Photo
        m = self.media
        if isinstance(m, MessageMediaPhoto):
            return m.photo
        if isinstance(m, Photo):
            return m
        return None

    @property
    def document(self):
        from ..types import MessageMediaDocument, Document
        m = self.media
        if isinstance(m, MessageMediaDocument):
            return m.document
        if isinstance(m, Document):
            return m
        return None

    @property
    def is_private(self) -> bool:
        from ..types import PeerUser
        return isinstance(getattr(self._raw, 'peer_id', None), PeerUser)

    @property
    def is_group(self) -> bool:
        from ..types import PeerChat, PeerChannel
        peer = getattr(self._raw, 'peer_id', None)
        return isinstance(peer, (PeerChat, PeerChannel))

    @property
    def chat_id(self) -> Optional[int]:
        from ...utils import get_peer_id
        peer = getattr(self._raw, 'peer_id', None)
        return get_peer_id(peer) if peer else None

    @property
    def sender_id(self) -> Optional[int]:
        from ...utils import get_peer_id
        from_id = getattr(self._raw, 'from_id', None)
        if from_id is not None:
            return get_peer_id(from_id)
        return self.chat_id

    # ── Actions ──────────────────────────────────────────────────────────────

    async def reply(self, text: str, **kwargs):
        """Reply to this message."""
        return await self._client.send_message(
            self.chat_id, text, reply_to=self.id, **kwargs)

    async def respond(self, text: str, **kwargs):
        """Send to the same chat without replying."""
        return await self._client.send_message(self.chat_id, text, **kwargs)

    async def edit(self, text: str, **kwargs):
        """Edit this message."""
        return await self._client.edit_message(
            self.chat_id, self.id, text, **kwargs)

    async def delete(self, *, revoke: bool = True):
        """Delete this message."""
        return await self._client.delete_messages(
            self.chat_id, [self.id], revoke=revoke)

    async def forward_to(self, entity):
        """Forward this message to another chat."""
        return await self._client.forward_messages(
            entity, [self.id], self.chat_id)

    async def pin(self, *, notify: bool = False):
        """Pin this message."""
        return await self._client.pin_message(
            self.chat_id, self.id, notify=notify)

    async def unpin(self):
        """Unpin this message."""
        return await self._client.unpin_message(self.chat_id, self.id)

    async def download_media(self, *args, **kwargs):
        """Download the media in this message."""
        return await self._client.download_media(self._raw, *args, **kwargs)

    async def get_reply_message(self):
        """Fetch the message this is replying to, or None."""
        rid = self.reply_to_msg_id
        if rid is None:
            return None
        msgs = await self._client.get_messages(
            self.chat_id, ids=[rid])
        return msgs[0] if msgs else None

    # ── Repr ─────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (f'Message(id={self.id}, chat={self.chat_id}, '
                f'text={self.text[:40]!r})')

    def __str__(self) -> str:
        return self.text
