"""
tglib/events/chataction.py  —  ChatAction event (joins, leaves, title changes…).

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
from typing import Optional, List
from .common import EventBuilder, EventCommon, name_inner_event
from ..utils import get_peer_id


@name_inner_event
class ChatAction(EventBuilder):
    """
    Fired on chat service messages: user joined/left, title changed, etc.

    Example
    -------
    ::

        @client.on(events.ChatAction)
        async def on_action(event):
            if event.user_joined:
                await event.respond(f'Welcome, {event.sender_id}!')
            elif event.user_left:
                await event.respond('Someone left!')
    """

    @classmethod
    def build(cls, update, others=None, self_id=None):
        from ..tl import types

        if isinstance(update, (types.UpdateChatParticipantAdd,
                                types.UpdateChatParticipantDelete,
                                types.UpdateChatParticipant)):
            return cls.Event(update)

        if isinstance(update, (types.UpdateChannelParticipant,)):
            return cls.Event(update)

        if isinstance(update, (types.UpdateNewMessage,
                                types.UpdateNewChannelMessage)):
            if isinstance(update.message, types.MessageService):
                return cls.Event(update)

        return None

    class Event(EventCommon):
        def __init__(self, update):
            self._update = update
            self._action = None

            from ..tl import types
            if isinstance(update, (types.UpdateNewMessage,
                                    types.UpdateNewChannelMessage)):
                msg = update.message
                self._peer    = getattr(msg, 'peer_id', None)
                self._from_id = getattr(msg, 'from_id', None)
                self._action  = getattr(msg, 'action', None)
                self._msg_id  = msg.id
            elif hasattr(update, 'chat_id'):
                self._peer    = types.PeerChat(update.chat_id)
                self._from_id = getattr(update, 'actor_id', None)
                self._action  = None
                self._msg_id  = None
            elif hasattr(update, 'channel_id'):
                self._peer    = types.PeerChannel(update.channel_id)
                self._from_id = getattr(update, 'actor_id', None)
                self._action  = None
                self._msg_id  = None
            else:
                self._peer    = None
                self._from_id = None
                self._msg_id  = None

        @property
        def chat_id(self) -> Optional[int]:
            if self._peer is None:
                return None
            return get_peer_id(self._peer)

        @property
        def sender_id(self) -> Optional[int]:
            if self._from_id is None:
                return None
            return get_peer_id(self._from_id)

        @property
        def action(self):
            return self._action

        @property
        def user_joined(self) -> bool:
            from ..tl import types
            return isinstance(self._action, (
                types.MessageActionChatAddUser,
                types.MessageActionChatJoinedByLink,
            ))

        @property
        def user_left(self) -> bool:
            from ..tl import types
            return isinstance(self._action, types.MessageActionChatDeleteUser)

        @property
        def user_kicked(self) -> bool:
            return self.user_left  # Telegram uses the same action

        @property
        def title_changed(self) -> bool:
            from ..tl import types
            return isinstance(self._action, types.MessageActionChatEditTitle)

        @property
        def new_title(self) -> Optional[str]:
            from ..tl import types
            if isinstance(self._action, types.MessageActionChatEditTitle):
                return self._action.title
            return None

        @property
        def photo_changed(self) -> bool:
            from ..tl import types
            return isinstance(self._action, (
                types.MessageActionChatEditPhoto,
                types.MessageActionChatDeletePhoto,
            ))

        @property
        def added_users(self) -> List[int]:
            from ..tl import types
            if isinstance(self._action, types.MessageActionChatAddUser):
                return list(self._action.users)
            return []

        @property
        def deleted_users(self) -> List[int]:
            from ..tl import types
            if isinstance(self._action, types.MessageActionChatDeleteUser):
                return [self._action.user_id]
            return []

        async def reply(self, text: str, **kwargs):
            return await self._client.send_message(
                self.chat_id, text, reply_to=self._msg_id, **kwargs
            )

        async def respond(self, text: str, **kwargs):
            return await self._client.send_message(self.chat_id, text, **kwargs)

        def __repr__(self) -> str:
            return (f'ChatAction.Event(chat={self.chat_id}, '
                    f'action={type(self._action).__name__})')
