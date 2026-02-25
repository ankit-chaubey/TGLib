"""
TelegramClient - the main entry point for tglib.

Improvements over the original:
  * In-memory EntityCache (ported from Telethon) for O(1) peer resolution
    without hitting SQLite on every send/forward.
  * get_input_entity() now follows the same priority chain as Telethon:
      1. direct InputPeer / 'me' / 'self'
      2. in-memory EntityCache
      3. SQLite session cache
      4. network fallback (ResolveUsername / GetUsers / GetChannels)
  * get_entity() batches GetUsers / GetChats / GetChannels calls efficiently.
  * __call__() handles flood waits, DC migration, and server errors with
    automatic retry (ported from Telethon UserMethods._call).
  * Entity cache is populated automatically after every API call that returns
    users/chats (process_entities on updates too).
  * _self_id property for quick self-reference.
"""

import asyncio
import logging
import os
import re
import time

from .crypto import AuthKey
from .entitycache import EntityCache
from .errors import (
    RPCError, FloodWaitError, SessionPasswordNeededError,
    PasswordHashInvalidError, rpc_message_to_error
)
from .network import MTProtoSender, make_connection
from .sessions import SQLiteSession, MemorySession
from . import helpers

__log__ = logging.getLogger(__name__)

DEFAULT_DC_ID          = 2
DEFAULT_DEVICE_MODEL   = 'tglib'
DEFAULT_SYSTEM_VERSION = 'Python'
DEFAULT_APP_VERSION    = '1.0'
DEFAULT_LANG_CODE      = 'en'
DEFAULT_LANG_PACK      = ''
DEFAULT_SYSTEM_LANG_CODE = 'en'
TL_LAYER = 222

# How long to sleep when the server explicitly asks for a flood wait
# that is below this threshold (seconds).
DEFAULT_FLOOD_SLEEP_THRESHOLD = 60


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
        device_model:      str  = DEFAULT_DEVICE_MODEL,
        system_version:    str  = DEFAULT_SYSTEM_VERSION,
        app_version:       str  = DEFAULT_APP_VERSION,
        lang_code:         str  = DEFAULT_LANG_CODE,
        system_lang_code:  str  = DEFAULT_SYSTEM_LANG_CODE,
        lang_pack:         str  = DEFAULT_LANG_PACK,
        dc_id:             int  = DEFAULT_DC_ID,
        test_mode:         bool = False,
        auto_reconnect:    bool = True,
        retries:           int  = 5,
        request_retries:   int  = 5,
        flood_sleep_threshold: int = DEFAULT_FLOOD_SLEEP_THRESHOLD,
    ):
        # Session
        if isinstance(session, str):
            self.session = SQLiteSession(session)
        elif session is None:
            self.session = MemorySession()
        else:
            self.session = session

        self.api_id   = api_id
        self.api_hash = api_hash

        self._device_model    = device_model
        self._system_version  = system_version
        self._app_version     = app_version
        self._lang_code       = lang_code
        self._system_lang_code = system_lang_code
        self._lang_pack       = lang_pack
        self._test_mode       = test_mode

        self._dc_id          = self.session.dc_id or dc_id
        self._sender: MTProtoSender = None
        self._updates_queue         = None   # lazy — created on first connect()
        self._updates_handlers      = []
        self._auto_reconnect        = auto_reconnect
        self._retries               = retries
        self._request_retries       = request_retries
        self.flood_sleep_threshold  = flood_sleep_threshold
        self._flood_waited_requests = {}     # CONSTRUCTOR_ID -> due timestamp
        self._inited                = False  # True after first invokeWithLayer

        # In-memory entity cache (Telethon-style)
        self._entity_cache = EntityCache()

        # Cached state
        self._connected              = False
        self._updates_task: asyncio.Task = None
        self._loop = None

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
        if self._connected:
            return

        if self._updates_queue is None:
            self._updates_queue = asyncio.Queue()

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
        self._inited    = False
        __log__.info('Connected to DC %d', self._dc_id)

        self._updates_task = asyncio.ensure_future(self._updates_dispatcher())

    async def disconnect(self):
        self._connected = False
        if self._updates_task and not self._updates_task.done():
            self._updates_task.cancel()
            try:
                await self._updates_task
            except (asyncio.CancelledError, Exception):
                pass
            self._updates_task = None
        if self._sender:
            await self._sender.disconnect()
        self.session.close()
        __log__.info('Disconnected')

    # ── Self-identity helpers ──────────────────────────────────────────────

    @property
    def _self_id(self):
        """Return the ID of the logged-in user, or None."""
        return self._entity_cache.self_id

    # ── Raw API invoke ──────────────────────────────────────────────────────

    async def __call__(self, request, ordered: bool = False,
                       flood_sleep_threshold: int = None):
        """
        Invoke a raw TL function and return its result.

        Automatically:
          - Wraps the first call with invokeWithLayer(initConnection(...))
          - Handles DC migration (PHONE_MIGRATE_X, NETWORK_MIGRATE_X, etc.)
          - Sleeps on FloodWaitError up to flood_sleep_threshold seconds
          - Retries on transient server errors (SERVER_ERROR, etc.)
        """
        if flood_sleep_threshold is None:
            flood_sleep_threshold = self.flood_sleep_threshold

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

        # Check if we're in a pending flood wait for this request type
        cid = getattr(getattr(request, 'query', request), 'CONSTRUCTOR_ID', None)
        if cid and cid in self._flood_waited_requests:
            due  = self._flood_waited_requests[cid]
            diff = round(due - time.time())
            if diff <= 3:
                self._flood_waited_requests.pop(cid, None)
            elif diff <= flood_sleep_threshold:
                __log__.info('Sleeping %ds (flood wait) for %s',
                             diff, type(request).__name__)
                await asyncio.sleep(diff)
                self._flood_waited_requests.pop(cid, None)
            else:
                raise FloodWaitError(request=request, capture=diff)

        last_error = None
        for attempt in range(self._request_retries):
            try:
                future = self._sender.send(request, ordered=ordered)
                result = await future

                # Populate entity cache and SQLite session from results
                self._populate_entity_cache(result)
                try:
                    self.session.process_entities(result)
                except Exception:
                    pass

                return result

            except RPCError as e:
                # DC migration
                m = re.match(
                    r'(?:PHONE|NETWORK|USER)_MIGRATE_(\d+)', e.message or '')
                if m:
                    new_dc = int(m.group(1))
                    __log__.info('Migrating to DC %d', new_dc)
                    await self._migrate_to_dc(new_dc)
                    inner = getattr(request, 'query', request)
                    inner = getattr(inner, 'query', inner)
                    return await self(inner, ordered=ordered)

                raise

            except FloodWaitError as e:
                last_error = e
                # Record it so subsequent calls know to wait
                if cid:
                    self._flood_waited_requests[cid] = time.time() + e.seconds
                secs = max(e.seconds, 1)
                if secs <= flood_sleep_threshold:
                    __log__.info('FloodWait %ds for %s', secs,
                                 type(request).__name__)
                    await asyncio.sleep(secs)
                else:
                    raise

            except Exception as e:
                last_error = e
                msg = str(e)
                # Transient server errors - retry after a brief sleep
                if any(x in msg for x in ('SERVER_ERROR', 'RPC_CALL_FAIL',
                                          'RPC_MCGET_FAIL', 'INTER_DC_CALL')):
                    __log__.warning('Transient server error, retrying: %s', e)
                    await asyncio.sleep(2)
                else:
                    raise

        raise last_error or RuntimeError(
            f'Request was unsuccessful {self._request_retries} time(s)')

    def _populate_entity_cache(self, result):
        """Update in-memory EntityCache from any result that carries users/chats."""
        if result is None:
            return
        users = getattr(result, 'users', []) or []
        chats = getattr(result, 'chats', []) or []
        if users or chats:
            self._entity_cache.extend(users, chats)

    async def _migrate_to_dc(self, dc_id: int):
        from .network.connection import DC_MAP
        if self._sender:
            await self._sender.disconnect()

        self._dc_id = dc_id
        ip, port = DC_MAP.get(dc_id, ('149.154.167.51', 443))
        self.session.set_dc(dc_id, ip, port)
        self.session.auth_key = AuthKey(None)
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
        self._inited = False

        if self._updates_task and not self._updates_task.done():
            self._updates_task.cancel()
            try:
                await self._updates_task
            except (asyncio.CancelledError, Exception):
                pass
        self._updates_task = asyncio.ensure_future(self._updates_dispatcher())

    # ── Auth ───────────────────────────────────────────────────────────────

    async def send_code_request(self, phone: str):
        from .tl.functions.auth import SendCodeRequest
        from .tl.types import CodeSettings
        return await self(SendCodeRequest(
            phone_number=phone,
            api_id=self.api_id,
            api_hash=self.api_hash,
            settings=CodeSettings(),
        ))

    async def sign_in(self, phone: str = None, code: str = None,
                      phone_code_hash: str = None, password: str = None):
        if password:
            return await self._sign_in_2fa(password)
        from .tl.functions.auth import SignInRequest
        return await self(SignInRequest(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code,
        ))

    async def _sign_in_2fa(self, password: str):
        from .tl.functions.account import GetPasswordRequest
        from .password import compute_srp_answer
        pwd = await self(GetPasswordRequest())
        from .tl.functions.auth import CheckPasswordRequest
        try:
            return await self(CheckPasswordRequest(
                password=await compute_srp_answer(pwd, password)
            ))
        except PasswordHashInvalidError:
            raise PasswordHashInvalidError() from None

    async def sign_in_bot(self, bot_token: str):
        from .tl.functions.auth import ImportBotAuthorizationRequest
        return await self(ImportBotAuthorizationRequest(
            flags=0,
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_auth_token=bot_token,
        ))

    async def log_out(self):
        from .tl.functions.auth import LogOutRequest
        await self(LogOutRequest())
        self.session.auth_key = AuthKey(None)
        self.session.save()

    async def is_user_authorized(self) -> bool:
        try:
            await self.get_me()
            return True
        except Exception:
            return False

    # ── High-level entity resolution (Telethon-style) ─────────────────────

    async def get_me(self):
        """Return the User object for the current logged-in account."""
        from .tl.functions.users import GetUsersRequest
        from .tl.types import InputUserSelf
        result = await self(GetUsersRequest(id=[InputUserSelf()]))
        user = result[0] if result else None
        if user and not self._entity_cache.self_id:
            self._entity_cache.set_self_user(
                user.id,
                getattr(user, 'bot', False),
                getattr(user, 'access_hash', 0) or 0
            )
        return user

    async def get_entity(self, entity):
        """
        Resolve entity to a full User/Chat/Channel object.

        Accepts:
          - 'me' / 'self'
          - @username or username string
          - +phone string
          - integer ID (positive = user/channel, negative = chat/channel)
          - Peer / InputPeer objects

        Efficient: batches same-type lookups into single API calls.
        """
        from .tl.functions.users import GetUsersRequest
        from .tl.functions.channels import GetChannelsRequest
        from .tl.functions.messages import GetChatsRequest
        from .tl.functions.contacts import ResolveUsernameRequest
        from .tl.types import (
            InputUserSelf, InputUser, InputChannel, PeerUser, PeerChat,
            PeerChannel
        )

        if isinstance(entity, str):
            if entity.lower() in ('me', 'self'):
                return await self.get_me()
            if entity.startswith('+'):
                row = self.session.get_entity_rows_by_phone(entity)
                if row:
                    return await self._get_user_by_id(row[0], row[1])
                raise ValueError(f'Entity not found for phone {entity}')
            username = entity.lstrip('@')
            result = await self(ResolveUsernameRequest(username=username))
            self._populate_entity_cache(result)
            try:
                self.session.process_entities(result)
            except Exception:
                pass
            if getattr(result, 'users', None):
                return result.users[0]
            if getattr(result, 'chats', None):
                return result.chats[0]
            raise ValueError(f'No entity found for username {username!r}')

        if isinstance(entity, int):
            return await self._get_entity_by_int_id(entity)

        # Peer objects
        if isinstance(entity, PeerUser):
            return await self._get_user_by_id(entity.user_id)
        if isinstance(entity, PeerChat):
            chats = (await self(GetChatsRequest(id=[entity.chat_id]))).chats
            return chats[0] if chats else None
        if isinstance(entity, PeerChannel):
            cached = self._entity_cache.get(entity.channel_id)
            ah = cached.hash if cached else 0
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=entity.channel_id, access_hash=ah)
            ]))
            self._populate_entity_cache(result)
            return result.chats[0] if result.chats else None

        # Already a full entity
        return entity

    async def _get_entity_by_int_id(self, entity_id: int):
        """Resolve an integer ID to a full entity, trying user then channel."""
        # Positive ID or known type from cache
        abs_id = abs(entity_id)
        cached = self._entity_cache.get(abs_id)
        if cached:
            ip = cached._as_input_peer()
            return await self.get_entity(ip)

        # Try session DB
        row = self.session.get_entity_rows_by_id(abs_id)
        if row:
            return await self._get_user_by_id(row[0], row[1])

        # Fallback: try as user (hash=0 works for bots/contacts)
        try:
            return await self._get_user_by_id(abs_id, 0)
        except Exception:
            pass

        # Fallback: try as channel
        try:
            from .tl.functions.channels import GetChannelsRequest
            from .tl.types import InputChannel
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=abs_id, access_hash=0)
            ]))
            self._populate_entity_cache(result)
            if result.chats:
                return result.chats[0]
        except Exception:
            pass

        raise ValueError(f'Could not find entity with ID {entity_id}')

    async def _get_user_by_id(self, user_id: int, access_hash: int = 0):
        from .tl.functions.users import GetUsersRequest
        from .tl.types import InputUser
        users = await self(GetUsersRequest(id=[
            InputUser(user_id=user_id, access_hash=access_hash)
        ]))
        if users:
            self._entity_cache.extend(users, [])
            try:
                self.session.process_entities(
                    type('_E', (), {'users': users, 'chats': []})())
            except Exception:
                pass
            return users[0]
        return None

    async def get_input_entity(self, peer):
        """
        Resolve peer to an InputPeer, following Telethon's priority chain:

          1. Already an InputPeer / 'me' / 'self'
          2. In-memory EntityCache (fastest, O(1))
          3. SQLite session cache
          4. Network (ResolveUsername / GetUsers / GetChannels)

        Raises ValueError if not resolvable.
        """
        from .tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel,
            InputPeerSelf, PeerUser, PeerChat, PeerChannel,
            InputPeerEmpty,
        )

        # 1. 'me' / 'self'
        if isinstance(peer, str) and peer.lower() in ('me', 'self'):
            return InputPeerSelf()

        # 2. Already InputPeer types
        if isinstance(peer, InputPeerUser):
            return peer
        if isinstance(peer, InputPeerChat):
            return peer
        if isinstance(peer, InputPeerChannel):
            return peer
        if isinstance(peer, InputPeerSelf):
            return peer
        if isinstance(peer, InputPeerEmpty):
            return peer

        # 3. Peer types - try entity cache then session, then network
        if isinstance(peer, PeerUser):
            uid = peer.user_id
            if uid == self._self_id:
                return InputPeerSelf()
            cached = self._entity_cache.get(uid)
            if cached:
                return cached._as_input_peer()
            row = self.session.get_entity_rows_by_id(uid)
            if row:
                return InputPeerUser(user_id=row[0], access_hash=row[1])
            entity = await self._get_user_by_id(uid, 0)
            if entity:
                return InputPeerUser(
                    user_id=entity.id,
                    access_hash=getattr(entity, 'access_hash', 0) or 0)
            return InputPeerUser(user_id=uid, access_hash=0)

        if isinstance(peer, PeerChat):
            return InputPeerChat(chat_id=peer.chat_id)

        if isinstance(peer, PeerChannel):
            cid = peer.channel_id
            cached = self._entity_cache.get(cid)
            if cached:
                return cached._as_input_peer()
            row = self.session.get_entity_rows_by_id(cid)
            if row:
                return InputPeerChannel(channel_id=row[0], access_hash=row[1])
            entity = await self._get_channel_by_id(cid, 0)
            if entity:
                return InputPeerChannel(
                    channel_id=entity.id,
                    access_hash=getattr(entity, 'access_hash', 0) or 0)
            return InputPeerChannel(channel_id=cid, access_hash=0)

        # 4. String: username or phone
        if isinstance(peer, str):
            if peer.startswith('+'):
                row = self.session.get_entity_rows_by_phone(peer)
                if row:
                    return InputPeerUser(user_id=row[0], access_hash=row[1])
            else:
                username = peer.lstrip('@')
                row = self.session.get_entity_rows_by_username(username)
                if row:
                    cached = self._entity_cache.get(row[0])
                    if cached:
                        return cached._as_input_peer()
                    return InputPeerUser(user_id=row[0], access_hash=row[1])
                # Must resolve via network
                entity = await self.get_entity(peer)
                return await self.get_input_entity(entity)

        # 5. Integer ID
        if isinstance(peer, int):
            abs_id = abs(peer)
            cached = self._entity_cache.get(abs_id)
            if cached:
                return cached._as_input_peer()
            row = self.session.get_entity_rows_by_id(abs_id)
            if row:
                # We don't know the type; make a best-effort guess
                entity = await self._get_user_by_id(row[0], row[1])
                if entity:
                    return InputPeerUser(user_id=row[0], access_hash=row[1])
            raise ValueError(
                f'Cannot find input entity for integer ID {peer}. '
                'Make sure you have seen this user/chat recently.')

        # 6. Full entity objects - extract from them
        eid   = getattr(peer, 'id', None)
        ehash = getattr(peer, 'access_hash', 0) or 0
        cls   = type(peer).__name__.lower()
        if eid is not None:
            if 'user' in cls:
                return InputPeerUser(user_id=eid, access_hash=ehash)
            elif 'chat' in cls and 'channel' not in cls:
                return InputPeerChat(chat_id=eid)
            elif 'channel' in cls or 'supergroup' in cls or 'megagroup' in cls:
                return InputPeerChannel(channel_id=eid, access_hash=ehash)

        raise ValueError(
            f'Cannot find the input entity for {peer!r} ({type(peer).__name__}). '
            'Please see https://docs.telethon.dev/en/stable/concepts/entities.html')

    async def _get_channel_by_id(self, channel_id: int, access_hash: int = 0):
        from .tl.functions.channels import GetChannelsRequest
        from .tl.types import InputChannel
        try:
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=channel_id, access_hash=access_hash)
            ]))
            chats = getattr(result, 'chats', [])
            if chats:
                self._populate_entity_cache(result)
                try:
                    self.session.process_entities(
                        type('_E', (), {'users': [], 'chats': chats})())
                except Exception:
                    pass
                return chats[0]
        except Exception:
            pass
        return None

    async def get_peer_id(self, peer, add_mark: bool = True) -> int:
        """Get the integer ID for a peer (bot-API style if add_mark=True)."""
        ip = await self.get_input_entity(peer)
        from .tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel, InputPeerSelf
        )
        if isinstance(ip, InputPeerSelf):
            if self._self_id:
                uid = self._self_id
                return uid if not add_mark else uid
        if isinstance(ip, InputPeerUser):
            return ip.user_id
        if isinstance(ip, InputPeerChat):
            return -ip.chat_id if add_mark else ip.chat_id
        if isinstance(ip, InputPeerChannel):
            return int(f'-100{ip.channel_id}') if add_mark else ip.channel_id
        raise ValueError(f'Cannot get peer_id from {ip!r}')

    # ── Messaging ──────────────────────────────────────────────────────────

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
        from .tl.functions.messages import SendMessageRequest
        from .tl.types import InputReplyToMessage

        peer = await self.get_input_entity(entity)
        text, entities = self._parse_text(message, parse_mode)

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

        return await self(SendMessageRequest(**kwargs))

    async def edit_message(self, entity, message_id: int, text: str, *,
                           parse_mode: str = None, link_preview: bool = True):
        from .tl.functions.messages import EditMessageRequest

        peer = await self.get_input_entity(entity)
        msg, entities = self._parse_text(text, parse_mode)
        return await self(EditMessageRequest(
            peer=peer,
            id=message_id,
            message=msg,
            no_webpage=not link_preview,
            entities=entities or None,
        ))

    async def delete_messages(self, entity, message_ids, *, revoke: bool = True):
        from .tl.functions.messages import DeleteMessagesRequest
        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]
        return await self(DeleteMessagesRequest(id=message_ids, revoke=revoke))

    async def get_messages(self, entity, limit: int = 100, *, offset_id: int = 0,
                           min_id: int = 0, max_id: int = 0):
        from .tl.functions.messages import GetHistoryRequest
        peer = await self.get_input_entity(entity)
        return await self(GetHistoryRequest(
            peer=peer, limit=limit, offset_id=offset_id,
            offset_date=0, add_offset=0,
            min_id=min_id, max_id=max_id, hash=0,
        ))

    async def get_dialogs(self, limit: int = 100):
        from .tl.functions.messages import GetDialogsRequest
        from .tl.types import InputPeerEmpty
        result = await self(GetDialogsRequest(
            offset_date=0, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=limit, hash=0,
        ))
        if result is None:
            __log__.warning('get_dialogs: server returned unknown type')
        return result

    async def get_participants(self, entity, limit: int = 200):
        from .tl.functions.channels import GetParticipantsRequest
        from .tl.types import ChannelParticipantsRecent, InputChannel

        peer = await self.get_input_entity(entity)
        if hasattr(peer, 'channel_id'):
            channel = InputChannel(channel_id=peer.channel_id,
                                   access_hash=peer.access_hash)
            return await self(GetParticipantsRequest(
                channel=channel,
                filter=ChannelParticipantsRecent(),
                offset=0, limit=limit, hash=0,
            ))
        raise ValueError('Entity is not a channel/supergroup')

    async def forward_messages(self, entity, message_ids, from_peer):
        from .tl.functions.messages import ForwardMessagesRequest
        to_peer   = await self.get_input_entity(entity)
        from_input = await self.get_input_entity(from_peer)
        if not isinstance(message_ids, list):
            message_ids = [message_ids]
        return await self(ForwardMessagesRequest(
            from_peer=from_input, id=message_ids, to_peer=to_peer,
            random_id=[helpers.generate_random_long() for _ in message_ids],
        ))

    async def pin_message(self, entity, message_id: int, *, notify: bool = False):
        from .tl.functions.messages import UpdatePinnedMessageRequest
        peer = await self.get_input_entity(entity)
        return await self(UpdatePinnedMessageRequest(
            peer=peer, id=message_id, silent=not notify
        ))

    # ── Text parsing ────────────────────────────────────────────────────────

    def _parse_text(self, text: str, parse_mode: str):
        if parse_mode in ('md', 'markdown'):
            return self._parse_markdown(text)
        if parse_mode == 'html':
            return self._parse_html(text)
        return text, []

    def _parse_markdown(self, text: str):
        import re
        from .tl.types import (MessageEntityBold, MessageEntityItalic,
                                MessageEntityCode)
        entities = []
        result   = text
        offset   = 0
        patterns = [
            (r'\*\*(.+?)\*\*', MessageEntityBold),
            (r'\*(.+?)\*',     MessageEntityItalic),
            (r'`(.+?)`',       MessageEntityCode),
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
        import html
        return html.unescape(text), []

    # ── Updates ─────────────────────────────────────────────────────────────

    def on(self, event_cls):
        def decorator(func):
            self._updates_handlers.append((event_cls, func))
            return func
        return decorator

    async def _updates_dispatcher(self):
        while self._connected:
            try:
                update = await asyncio.wait_for(
                    self._updates_queue.get(), timeout=1.0)
                # Keep entity cache warm from updates
                try:
                    self._populate_entity_cache(update)
                    self.session.process_entities(update)
                except Exception:
                    pass
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
        try:
            while self._connected:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.disconnect()

    # ── Sync helpers ─────────────────────────────────────────────────────────

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_sync(self, coro):
        return self._get_loop().run_until_complete(coro)

    def run(self, coro):
        return self._run_sync(coro)

    # ── Context managers ─────────────────────────────────────────────────────

    def __enter__(self):
        self._run_sync(self.start())
        return self

    def __exit__(self, *args):
        try:
            self._run_sync(self.disconnect())
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
                self._loop = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── Start ─────────────────────────────────────────────────────────────────

    async def start(self, phone=None, password=None, bot_token=None,
                    code_callback=None):
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
