"""
tglib/events/messagedeleted.py  —  MessageDeleted event.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
from typing import List, Optional
from .common import EventBuilder, EventCommon, name_inner_event


@name_inner_event
class MessageDeleted(EventBuilder):
    """
    Fired whenever one or more messages are deleted.

    Note: Telegram does not always report message deletions for privacy
    reasons, so not every deletion will trigger this event.

    Example
    -------
    ::

        @client.on(events.MessageDeleted)
        async def on_delete(event):
            print(f'{len(event.deleted_ids)} message(s) deleted')
    """

    @classmethod
    def build(cls, update, others=None, self_id=None):
        from ..tl import types
        if isinstance(update, types.UpdateDeleteMessages):
            return cls.Event(deleted_ids=update.messages, channel_id=None)
        if isinstance(update, types.UpdateDeleteChannelMessages):
            return cls.Event(
                deleted_ids=update.messages,
                channel_id=update.channel_id,
            )
        return None

    class Event(EventCommon):
        """
        Attributes
        ----------
        deleted_ids : List[int]
            The IDs of the deleted messages.
        deleted_id : int
            The ID of the first deleted message (shortcut).
        """

        def __init__(self, deleted_ids: List[int], channel_id: Optional[int]):
            self.deleted_ids = deleted_ids
            self._channel_id = channel_id

        @property
        def deleted_id(self) -> Optional[int]:
            return self.deleted_ids[0] if self.deleted_ids else None

        @property
        def chat_id(self) -> Optional[int]:
            if self._channel_id is None:
                return None
            return int('-100' + str(self._channel_id))

        async def reply(self, *args, **kwargs):
            raise TypeError('Cannot reply to a deleted message event')

        async def respond(self, *args, **kwargs):
            raise TypeError('Cannot respond to a deleted message event')

        def __repr__(self) -> str:
            return (f'MessageDeleted.Event(ids={self.deleted_ids}, '
                    f'channel={self._channel_id})')
