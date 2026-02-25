"""
tglib/events/callbackquery.py  —  CallbackQuery event (bot inline keyboards).

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import re
from typing import Optional, Union
from .common import EventBuilder, EventCommon, name_inner_event
from ..utils import get_peer_id


@name_inner_event
class CallbackQuery(EventBuilder):
    """
    Fired when a user clicks an inline keyboard button.

    Parameters
    ----------
    data : bytes | str | Pattern, optional
        Filter by the callback data.  If str, it's treated as a regex.

    Example
    -------
    ::

        @client.on(events.CallbackQuery(data=b'action_1'))
        async def on_btn(event):
            await event.answer('You clicked button 1!')
            await event.edit('Updated message')
    """

    def __init__(self, chats=None, *, blacklist_chats=False, func=None, data=None):
        super().__init__(chats, blacklist_chats=blacklist_chats, func=func)

        if isinstance(data, bytes):
            self.data = data
            self._data_re = None
        elif isinstance(data, str):
            self.data = None
            self._data_re = re.compile(data.encode('utf-8') if isinstance(data, str) else data)
        elif data is None or callable(data):
            self.data = None
            self._data_re = None
            self._data_func = data
        elif hasattr(data, 'match'):
            self.data = None
            self._data_re = data
        else:
            raise TypeError(f'Invalid data filter type: {type(data).__name__}')
        self._data_func = None

    @classmethod
    def build(cls, update, others=None, self_id=None):
        from ..tl import types
        if isinstance(update, types.UpdateBotCallbackQuery):
            return cls.Event(update)
        if isinstance(update, types.UpdateInlineBotCallbackQuery):
            return cls.Event(update)
        return None

    def filter(self, event):
        if self.data is not None and event.data != self.data:
            return None
        if self._data_re is not None:
            m = self._data_re.match(event.data or b'')
            if not m:
                return None
            event.data_match = m
        return super().filter(event)

    class Event(EventCommon):
        """
        Attributes
        ----------
        data : bytes
            The callback data attached to the button.
        query : UpdateBotCallbackQuery
            The raw update object.
        """

        def __init__(self, query):
            self.query      = query
            self.data_match = None
            self._answered  = False

        @property
        def data(self) -> Optional[bytes]:
            return getattr(self.query, 'data', None)

        @property
        def id(self) -> int:
            return self.query.query_id

        @property
        def chat_id(self) -> Optional[int]:
            peer = getattr(self.query, 'peer', None)
            if peer is None:
                return None
            return get_peer_id(peer)

        @property
        def sender_id(self) -> Optional[int]:
            return getattr(self.query, 'user_id', None)

        @property
        def message_id(self) -> Optional[int]:
            return getattr(self.query, 'msg_id', None)

        async def answer(
            self,
            message: str = None,
            *,
            alert: bool = False,
            url: str = None,
            cache_time: int = 0,
        ):
            """
            Answer the callback query (sends a toast / alert to the user).

            Parameters
            ----------
            message : str, optional
                Text to show in the toast notification.
            alert : bool
                If True, show a full alert dialog instead of a toast.
            url : str, optional
                URL to open (for ``game`` type queries).
            cache_time : int
                Seconds to cache the answer client-side.
            """
            from ..tl.functions.messages import SetBotCallbackAnswerRequest
            if self._answered:
                return
            self._answered = True
            return await self._client(SetBotCallbackAnswerRequest(
                query_id=self.id,
                message=message,
                alert=alert,
                url=url,
                cache_time=cache_time,
            ))

        async def edit(self, text: str = None, *, buttons=None, **kwargs):
            """Edit the message that contains the clicked button."""
            return await self._client.edit_message(
                self.chat_id, self.message_id, text,
                buttons=buttons, **kwargs,
            )

        async def delete(self):
            """Delete the message that contains the clicked button."""
            return await self._client.delete_messages(
                self.chat_id, [self.message_id]
            )

        async def reply(self, text: str, **kwargs):
            return await self._client.send_message(
                self.chat_id, text, reply_to=self.message_id, **kwargs
            )

        async def respond(self, text: str, **kwargs):
            return await self._client.send_message(self.chat_id, text, **kwargs)

        def __repr__(self) -> str:
            return (f'CallbackQuery.Event(id={self.id}, '
                    f'data={self.data!r}, sender={self.sender_id})')
