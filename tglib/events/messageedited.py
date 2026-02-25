"""
tglib/events/messageedited.py  —  MessageEdited event.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
from .newmessage import NewMessage
from .common import name_inner_event


@name_inner_event
class MessageEdited(NewMessage):
    """
    Fired whenever a message is edited.

    Behaves exactly like ``NewMessage`` but triggers on
    ``UpdateEditMessage`` / ``UpdateEditChannelMessage``.

    Example
    -------
    ::

        @client.on(events.MessageEdited)
        async def on_edit(event):
            print('Edited:', event.text)
    """

    @classmethod
    def build(cls, update, others=None, self_id=None):
        from ..tl import types
        if isinstance(update, (types.UpdateEditMessage,
                                types.UpdateEditChannelMessage)):
            if not isinstance(update.message, types.Message):
                return None
            return cls.Event(update.message)
        return None

    class Event(NewMessage.Event):
        pass
