"""
TelegramClient - the main entry point for tglib.
Provides a high-level API similar to Telethon and Pyrogram.
"""
import asyncio
import logging
import os

from .crypto import AuthKey
from .errors import (
    RPCError, FloodWaitError, SessionPasswordNeededError,
    PasswordHashInvalidError, rpc_message_to_error
)
from .network import MTProtoSender, make_connection
from .sessions import SQLiteSession, MemorySession
from . import helpers

__log__ = logging.getLogger(__name__)

DEFAULT_DC_ID = 2
DEFAULT_DEVICE_MODEL = 'tglib'
DEFAULT_SYSTEM_VERSION = 'Python'
DEFAULT_APP_VERSION = '1.0'
DEFAULT_LANG_CODE = 'en'
DEFAULT_LANG_PACK = ''
DEFAULT_SYSTEM_LANG_CODE = 'en'
TL_LAYER = 222  # Match Telethon; avoids server sending layer 225+ unknown types


class TelegramClient:
    """
    High-level Telegram client using MTProto.

    Usage:
        async with TelegramClient('my_session', api_id, api_hash) as client:
            me = await client.get_me()
            print(me.first_name)
            await client.send_message('username', 'Hello from tglib!')
    """

    def __init__(
        self,
        session,
        api_id: int,
        api_hash: str,
        *,
        device_model: str = DEFAULT_DEVICE_MODEL,
        system_version: str = DEFAULT_SYSTEM_VERSION,
        app_version: str = DEFAULT_APP_VERSION,
        lang_code: str = DEFAULT_LANG_CODE,
        system_lang_code: str = DEFAULT_SYSTEM_LANG_CODE,
        lang_pack: str = DEFAULT_LANG_PACK,
        dc_id: int = DEFAULT_DC_ID,
        test_mode: bool = False,
        auto_reconnect: bool = True,
        retries: int = 5,
    ):
        # Session
        if isinstance(session, str):
            self.session = SQLiteSession(session)
        elif session is None:
            self.session = MemorySession()
        else:
            self.session = session

        self.api_id = api_id
        self.api_hash = api_hash
        self._device_model = device_model
        self._system_version = system_version
        self._app_version = app_version
        self._lang_code = lang_code
        self._system_lang_code = system_lang_code
        self._lang_pack = lang_pack
        self._test_mode = test_mode

        self._dc_id = self.session.dc_id or dc_id
        self._sender = None
        self._updates_queue = asyncio.Queue()
        self._updates_handlers = []
        self._auto_reconnect = auto_reconnect
        self._retries = retries
        self._inited = False  # becomes True after first invokeWithLayer

        # Cached state
        self._self_input_peer = None
        self._connected = False

        # Sync loop (used when calling client in non-async context)
        self._loop = None

        # Logging
        self._loggers = {
            name: logging.getLogger(name)
            for name in (
                __name__,
                'tglib.network.mtprotosender',
                'tglib.network.mtprotostate',
                'tglib.network.connection',
                'tglib.network.mtprotoplainsender',
                'tglib.extensions.messagepacker',
            )
        }

    # ── Connection ─────────────────────────────────────────────────────────

    async def connect(self):
        """Connect to Telegram and establish an encrypted session."""
        if self._connected:
            return

        conn = make_connection(self._dc_id, test=self._test_mode,
                               loggers=self._loggers)

        async def on_auth_key(key: AuthKey):
            self.session.auth_key = key
            self.session.save()

        self._sender = MTProtoSender(
            auth_key=self.session.auth_key,
            loggers=self._loggers,
            retries=self._retries,
            auto_reconnect=self._auto_reconnect,
            auth_key_callback=on_auth_key,
            updates_queue=self._updates_queue,
        )
        await self._sender.connect(conn)
        self._connected = True
        self._inited = False  # reset so next call sends invokeWithLayer again
        __log__.info('Connected to DC %d', self._dc_id)

        # Start updates dispatcher
        asyncio.ensure_future(self._updates_dispatcher())

    async def disconnect(self):
        """Gracefully disconnect from Telegram."""
        if self._sender:
            await self._sender.disconnect()
        self.session.close()
        self._connected = False
        __log__.info('Disconnected')

    # ── Raw API invoke ──────────────────────────────────────────────────────

    async def __call__(self, request, ordered: bool = False):
        """
        Invoke a raw TL function and return its result.
        Automatically wraps the first call with invokeWithLayer(initConnection(...)).
        Silently handles DC migration (PHONE_MIGRATE_X, NETWORK_MIGRATE_X, etc).
        """
        import re
        if not self._inited:
            from .tl.functions import InvokeWithLayerRequest, InitConnectionRequest
            request = InvokeWithLayerRequest(
                layer=TL_LAYER,
                query=InitConnectionRequest(
                    api_id=self.api_id,
                    device_model=self._device_model,
                    system_version=self._system_version,
                    app_version=self._app_version,
                    lang_code=self._lang_code,
                    lang_pack=self._lang_pack,
                    system_lang_code=self._system_lang_code,
                    query=request,
                    proxy=None,
                    params=None,
                )
            )
            self._inited = True

        try:
            future = self._sender.send(request, ordered=ordered)
            return await future
        except RPCError as e:
            m = re.match(r'(?:PHONE|NETWORK|USER)_MIGRATE_(\d+)', e.message)
            if m:
                new_dc = int(m.group(1))
                __log__.info('Migrating to DC %d', new_dc)
                await self._migrate_to_dc(new_dc)
                # Unwrap InvokeWithLayer(InitConnection(original_request)) → original_request
                # Then re-invoke via self() so invokeWithLayer is re-applied on the new DC
                inner = getattr(request, 'query', request)   # InitConnectionRequest or request
                inner = getattr(inner, 'query', inner)        # original request
                return await self(inner, ordered=ordered)
            raise

    async def _migrate_to_dc(self, dc_id: int):
        """Switch connection to a different DC, regenerating the auth key."""
        from .network.connection import DC_MAP
        if self._sender:
            await self._sender.disconnect()

        self._dc_id = dc_id
        # Use set_dc() — dc_id is a read-only property; set_dc() is the correct setter
        ip, port = DC_MAP.get(dc_id, ('149.154.167.51', 443))
        self.session.set_dc(dc_id, ip, port)
        self.session.auth_key = AuthKey(None)  # force new auth key on new DC
        self.session.save()

        conn = make_connection(self._dc_id, test=self._test_mode,
                               loggers=self._loggers)

        async def on_auth_key(key: AuthKey):
            self.session.auth_key = key
            self.session.save()

        self._sender = MTProtoSender(
            auth_key=self.session.auth_key,
            loggers=self._loggers,
            retries=self._retries,
            auto_reconnect=self._auto_reconnect,
            auth_key_callback=on_auth_key,
            updates_queue=self._updates_queue,
        )
        await self._sender.connect(conn)
        self._inited = False  # must re-init on new DC
        asyncio.ensure_future(self._updates_dispatcher())

    # ── Auth ───────────────────────────────────────────────────────────────

    async def send_code_request(self, phone: str):
        """Send authentication code to the given phone number."""
        from .tl.functions.auth import SendCodeRequest
        from .tl.types import CodeSettings

        result = await self(SendCodeRequest(
            phone_number=phone,
            api_id=self.api_id,
            api_hash=self.api_hash,
            settings=CodeSettings(),
        ))
        return result

    async def sign_in(self, phone: str = None, code: str = None,
                      phone_code_hash: str = None, password: str = None):
        """
        Sign in with phone number + code, or 2FA password.
        Call send_code_request first to get phone_code_hash.
        """
        if password:
            return await self._sign_in_2fa(password)

        from .tl.functions.auth import SignInRequest
        result = await self(SignInRequest(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code,
        ))
        return result

    async def _sign_in_2fa(self, password: str):
        """Handle 2FA (SRP) authentication.

        Raises PasswordHashInvalidError if the password is wrong.
        """
        from .tl.functions.account import GetPasswordRequest
        from .password import compute_srp_answer
        # Always fetch fresh srp_B / srp_id — they expire quickly
        pwd = await self(GetPasswordRequest())
        from .tl.functions.auth import CheckPasswordRequest
        try:
            result = await self(CheckPasswordRequest(
                password=await compute_srp_answer(pwd, password)
            ))
        except PasswordHashInvalidError:
            raise PasswordHashInvalidError() from None   # re-raise with clean traceback
        return result

    async def sign_in_bot(self, bot_token: str):
        """Sign in as a bot using a bot token."""
        from .tl.functions.auth import ImportBotAuthorizationRequest
        result = await self(ImportBotAuthorizationRequest(
            flags=0,
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_auth_token=bot_token,
        ))
        return result

    async def log_out(self):
        """Log out from the current session."""
        from .tl.functions.auth import LogOutRequest
        await self(LogOutRequest())
        self.session.auth_key = AuthKey(None)
        self.session.save()

    async def is_user_authorized(self) -> bool:
        """Check if the current session is authorized."""
        try:
            await self.get_me()
            return True
        except Exception:
            return False

    # ── High-level methods ──────────────────────────────────────────────────

    async def get_me(self):
        """Return the User object for the current logged-in account."""
        from .tl.functions.users import GetFullUserRequest
        from .tl.types import InputUserSelf
        result = await self(GetFullUserRequest(id=InputUserSelf()))
        return result.users[0] if hasattr(result, 'users') else result.user

    async def get_entity(self, entity):
        """
        Resolve entity to a full User/Chat/Channel object.
        Accepts username (str), phone, integer ID, or a Peer object.
        """
        from .tl.functions.users import GetUsersRequest
        from .tl.functions.contacts import ResolveUsernameRequest
        from .tl.types import InputUserSelf

        if isinstance(entity, str):
            if entity.startswith('+'):
                # Phone number
                row = self.session.get_entity_rows_by_phone(entity)
                if row:
                    return await self._get_entity_by_id(row[0], row[1])
                raise ValueError(f'Could not find entity with phone {entity}')
            else:
                # Username
                username = entity.lstrip('@')
                result = await self(ResolveUsernameRequest(username=username))
                self.session.process_entities(result)
                return result.users[0] if result.users else result.chats[0]
        elif isinstance(entity, int):
            return await self._get_entity_by_id(entity)
        return entity

    async def _get_entity_by_id(self, entity_id: int, access_hash: int = 0):
        from .tl.functions.users import GetUsersRequest
        from .tl.types import InputUser
        users = await self(GetUsersRequest(id=[
            InputUser(user_id=entity_id, access_hash=access_hash)
        ]))
        return users[0] if users else None

    async def get_input_entity(self, peer):
        """Resolve to an InputPeer."""
        from .tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel,
            InputPeerSelf, PeerUser, PeerChat, PeerChannel
        )
        if isinstance(peer, str) and peer in ('me', 'self'):
            return InputPeerSelf()

        entity = await self.get_entity(peer)
        if entity is None:
            raise ValueError(f'Cannot find entity: {peer}')

        eid = getattr(entity, 'id', None)
        ehash = getattr(entity, 'access_hash', 0) or 0
        classname = type(entity).__name__.lower()

        if 'user' in classname:
            return InputPeerUser(user_id=eid, access_hash=ehash)
        elif 'chat' in classname:
            return InputPeerChat(chat_id=eid)
        elif 'channel' in classname:
            return InputPeerChannel(channel_id=eid, access_hash=ehash)
        raise ValueError(f'Unknown entity type: {type(entity)}')

    async def send_message(
        self,
        entity,
        message: str,
        *,
        reply_to=None,
        parse_mode: str = None,
        link_preview: bool = True,
        silent: bool = False,
        clear_draft: bool = False,
        schedule_date=None,
    ):
        """
        Send a text message to an entity.

        Args:
            entity: Username, phone, ID, or Peer object
            message: Text to send
            reply_to: Message ID to reply to
            parse_mode: 'md', 'markdown', 'html', or None
            link_preview: Whether to show link previews
            silent: Whether to send silently
        """
        from .tl.functions.messages import SendMessageRequest
        from .tl.types import InputReplyToMessage

        peer = await self.get_input_entity(entity)
        entities = []
        text = message

        if parse_mode in ('md', 'markdown'):
            text, entities = self._parse_markdown(message)
        elif parse_mode == 'html':
            text, entities = self._parse_html(message)

        kwargs = dict(
            peer=peer,
            message=text,
            random_id=helpers.generate_random_long(),
            no_webpage=not link_preview,
            silent=silent,
            clear_draft=clear_draft,
            entities=entities or None,
        )

        if reply_to is not None:
            kwargs['reply_to'] = InputReplyToMessage(reply_to_msg_id=reply_to)

        if schedule_date:
            kwargs['schedule_date'] = schedule_date

        result = await self(SendMessageRequest(**kwargs))
        return result

    async def edit_message(self, entity, message_id: int, text: str, *,
                           parse_mode: str = None, link_preview: bool = True):
        """Edit an existing message."""
        from .tl.functions.messages import EditMessageRequest
        from .tl.types import InputMessageID

        peer = await self.get_input_entity(entity)
        entities = []
        msg = text
        if parse_mode in ('md', 'markdown'):
            msg, entities = self._parse_markdown(text)
        elif parse_mode == 'html':
            msg, entities = self._parse_html(text)

        return await self(EditMessageRequest(
            peer=peer,
            id=message_id,
            message=msg,
            no_webpage=not link_preview,
            entities=entities or None,
        ))

    async def delete_messages(self, entity, message_ids, *, revoke: bool = True):
        """Delete one or more messages."""
        from .tl.functions.messages import DeleteMessagesRequest

        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]
        return await self(DeleteMessagesRequest(id=message_ids, revoke=revoke))

    async def get_messages(self, entity, limit: int = 100, *, offset_id: int = 0,
                           min_id: int = 0, max_id: int = 0):
        """Get messages from a chat/user."""
        from .tl.functions.messages import GetHistoryRequest
        from .tl.types import InputMessagesFilterEmpty

        peer = await self.get_input_entity(entity)
        return await self(GetHistoryRequest(
            peer=peer,
            limit=limit,
            offset_id=offset_id,
            offset_date=None,
            add_offset=0,
            min_id=min_id,
            max_id=max_id,
            hash=0,
        ))

    async def get_dialogs(self, limit: int = 100):
        """Get recent dialogs (chats/channels/users)."""
        from .tl.functions.messages import GetDialogsRequest
        from .tl.types import InputPeerEmpty

        return await self(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=limit,
            hash=0,
        ))

    async def get_participants(self, entity, limit: int = 200):
        """Get participants of a group/channel."""
        from .tl.functions.channels import GetParticipantsRequest
        from .tl.types import ChannelParticipantsRecent, InputChannel, InputChannelFromMessage

        peer = await self.get_input_entity(entity)
        # Convert to InputChannel if needed
        if hasattr(peer, 'channel_id'):
            channel = InputChannel(channel_id=peer.channel_id,
                                   access_hash=peer.access_hash)
            return await self(GetParticipantsRequest(
                channel=channel,
                filter=ChannelParticipantsRecent(),
                offset=0,
                limit=limit,
                hash=0,
            ))
        raise ValueError('Entity is not a channel/supergroup')

    async def forward_messages(self, entity, message_ids, from_peer):
        """Forward messages from one chat to another."""
        from .tl.functions.messages import ForwardMessagesRequest

        to_peer = await self.get_input_entity(entity)
        from_input = await self.get_input_entity(from_peer)
        if not isinstance(message_ids, list):
            message_ids = [message_ids]
        return await self(ForwardMessagesRequest(
            from_peer=from_input,
            id=message_ids,
            to_peer=to_peer,
            random_id=[helpers.generate_random_long() for _ in message_ids],
        ))

    async def pin_message(self, entity, message_id: int, *, notify: bool = False):
        """Pin a message in a chat."""
        from .tl.functions.messages import UpdatePinnedMessageRequest
        peer = await self.get_input_entity(entity)
        return await self(UpdatePinnedMessageRequest(
            peer=peer, id=message_id, silent=not notify
        ))

    # ── Text parsing ────────────────────────────────────────────────────────

    def _parse_markdown(self, text: str):
        """Simple markdown parser. Returns (plain_text, [MessageEntity])."""
        import re
        from .tl.types import (MessageEntityBold, MessageEntityItalic,
                                MessageEntityCode, MessageEntityPre)
        entities = []
        # Very simplified; a full parser would handle all cases
        result = text
        offset = 0
        patterns = [
            (r'\*\*(.+?)\*\*', MessageEntityBold),
            (r'\*(.+?)\*', MessageEntityItalic),
            (r'`(.+?)`', MessageEntityCode),
        ]
        for pattern, cls in patterns:
            for m in re.finditer(pattern, result):
                start = m.start() - offset
                inner = m.group(1)
                entities.append(cls(offset=start, length=len(inner)))
                result = result[:m.start() - offset] + inner + result[m.end() - offset:]
                offset += len(m.group(0)) - len(inner)
        return result, entities

    def _parse_html(self, text: str):
        """Simple HTML parser. Returns (plain_text, [MessageEntity])."""
        import html
        # Minimal implementation; use a proper parser for production
        plain = html.unescape(text)
        return plain, []

    # ── Updates ─────────────────────────────────────────────────────────────

    def on(self, event_cls):
        """Decorator to register an event handler."""
        def decorator(func):
            self._updates_handlers.append((event_cls, func))
            return func
        return decorator

    async def _updates_dispatcher(self):
        """Process incoming updates and dispatch to handlers."""
        while self._connected:
            try:
                update = await asyncio.wait_for(
                    self._updates_queue.get(), timeout=1.0
                )
                for event_cls, handler in self._updates_handlers:
                    try:
                        if event_cls is None or isinstance(update, event_cls):
                            if asyncio.iscoroutinefunction(handler):
                                await handler(update)
                            else:
                                handler(update)
                    except Exception as e:
                        __log__.error('Handler error: %s', e, exc_info=True)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def run_until_disconnected(self):
        """
        Block until the client disconnects.

        Async usage:
            await client.run_until_disconnected()

        Sync usage (inside 'with' block):
            client.run(client.run_until_disconnected())
        """
        try:
            while self._connected:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.disconnect()

    # ── Sync helpers ─────────────────────────────────────────────────────────

    def _get_loop(self):
        """Get or create a dedicated event loop for sync usage."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_sync(self, coro):
        """Run a coroutine synchronously. Used internally for sync wrappers."""
        return self._get_loop().run_until_complete(coro)

    def run(self, coro):
        """
        Run any coroutine synchronously. Useful for simple scripts.

        Example:
            me = client.run(client.get_me())
            print(me.first_name)
        """
        return self._run_sync(coro)

    # ── Context manager (sync) ────────────────────────────────────────────────

    def __enter__(self):
        """
        Sync context manager support.

        Example:
            with TelegramClient(...) as client:
                me = client.run(client.get_me())
        """
        self._run_sync(self.start())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._run_sync(self.disconnect())
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
                self._loop = None

    # ── Context manager (async) ───────────────────────────────────────────────

    async def __aenter__(self):
        """
        Async context manager support.

        Example:
            async with TelegramClient(...) as client:
                me = await client.get_me()
        """
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    # ── Start ─────────────────────────────────────────────────────────────────

    async def start(self, phone=None, password=None, bot_token=None,
                    code_callback=None):
        """
        Connect and authenticate. Works in both async and sync contexts.

        Async usage:
            await client.start(phone='+1234567890')

        Sync usage (via __enter__):
            with client:
                ...
        """
        await self.connect()
        if await self.is_user_authorized():
            return self

        if bot_token:
            await self.sign_in_bot(bot_token)
            return self

        if phone:
            sent = await self.send_code_request(phone)
            code = (code_callback() if code_callback
                    else input('Enter the code: '))
            try:
                await self.sign_in(phone, code,
                                   phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                pwd = password or input('Enter 2FA password: ')
                await self._sign_in_2fa(pwd)
        return self
