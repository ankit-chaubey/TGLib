"""
tglib/client.py  —  Production-grade TelegramClient.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib
Contact : ankitchaubey.dev@gmail.com

Core architecture inspired by Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.

Feature summary
---------------
Connection & Auth
  connect / disconnect / start / is_user_authorized
  send_code_request / sign_in / sign_in_bot / sign_up / log_out / qr_login

Peer resolution (Telethon-compatible priority chain)
  get_me / get_entity / get_input_entity / get_peer_id

Messaging
  send_message / edit_message / delete_messages / get_messages
  iter_messages / forward_messages / pin_message / unpin_message / unpin_all_messages
  search_messages

Media
  send_file / upload_file / download_media / download_file / iter_download

Dialogs & Participants
  get_dialogs / iter_dialogs / get_participants / iter_participants

Cache & sessions
  entity cache (LRU + TTL), SQLite + memory + string sessions

Event system
  on() decorator, add_event_handler / remove_event_handler
  run_until_disconnected, all Telethon-compatible event classes

Raw API
  await client(SomeRequest(...))  — full flood-wait, DC-migrate, retry logic
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import pathlib
import re
import time
from typing import Optional, Union, List, Callable, Any

from .crypto import AuthKey
from .entitycache import EntityCache
from .errors import (
    RPCError, FloodWaitError, SessionPasswordNeededError,
    PasswordHashInvalidError, rpc_message_to_error,
)
from .network import MTProtoSender, make_connection
from .sessions import SQLiteSession, MemorySession
from . import helpers, utils

__log__ = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_DC_ID              = 2
DEFAULT_DEVICE_MODEL       = 'tglib'
DEFAULT_SYSTEM_VERSION     = 'Python'
DEFAULT_APP_VERSION        = '1.0'
DEFAULT_LANG_CODE          = 'en'
DEFAULT_LANG_PACK          = ''
DEFAULT_SYSTEM_LANG_CODE   = 'en'
TL_LAYER                   = 223
DEFAULT_FLOOD_SLEEP_THRESHOLD = 60

# Upload / download chunk size limits (Telegram enforced)
MIN_CHUNK_SIZE = 4 * 1024          #   4 KB
MAX_CHUNK_SIZE = 512 * 1024        # 512 KB
BIG_FILE_THRESHOLD = 10 * 1024 * 1024  # 10 MB

# Maximum messages per GetHistory call
_MAX_CHUNK_MESSAGES = 100


# ══════════════════════════════════════════════════════════════════════════════
#  TelegramClient
# ══════════════════════════════════════════════════════════════════════════════

class TelegramClient:
    """
    Full-featured Telegram MTProto client compatible with bots and userbots.

    Quick start (userbot)::

        async with TelegramClient('session', api_id, api_hash) as client:
            me = await client.get_me()
            print(me.first_name)
            await client.send_message('me', 'Hello from tglib!')

    Quick start (bot)::

        async with TelegramClient('bot', api_id, api_hash) as client:
            await client.start(bot_token='TOKEN:HERE')
            await client.run_until_disconnected()
    """

    def __init__(
        self,
        session,
        api_id: int,
        api_hash: str,
        *,
        device_model:          str  = DEFAULT_DEVICE_MODEL,
        system_version:        str  = DEFAULT_SYSTEM_VERSION,
        app_version:           str  = DEFAULT_APP_VERSION,
        lang_code:             str  = DEFAULT_LANG_CODE,
        system_lang_code:      str  = DEFAULT_SYSTEM_LANG_CODE,
        lang_pack:             str  = DEFAULT_LANG_PACK,
        dc_id:                 int  = DEFAULT_DC_ID,
        test_mode:             bool = False,
        auto_reconnect:        bool = True,
        retries:               int  = 5,
        request_retries:       int  = 5,
        flood_sleep_threshold: int  = DEFAULT_FLOOD_SLEEP_THRESHOLD,
        entity_cache_ttl:      float = 3600,
        entity_cache_size:     int   = 10_000,
    ):
        # ── Session ────────────────────────────────────────────────────────
        if isinstance(session, str):
            self.session = SQLiteSession(session)
        elif session is None:
            self.session = MemorySession()
        else:
            self.session = session

        self.api_id   = api_id
        self.api_hash = api_hash

        self._device_model     = device_model
        self._system_version   = system_version
        self._app_version      = app_version
        self._lang_code        = lang_code
        self._system_lang_code = system_lang_code
        self._lang_pack        = lang_pack
        self._test_mode        = test_mode

        self._dc_id    = self.session.dc_id or dc_id
        self._sender: Optional[MTProtoSender] = None

        # Event / update handling
        self._updates_queue: Optional[asyncio.Queue] = None
        self._updates_task:  Optional[asyncio.Task]  = None
        self._event_handlers: list = []   # [(builder_instance, handler_fn)]
        self._raw_handlers:   list = []   # [(type_filter, handler_fn)]

        self._auto_reconnect        = auto_reconnect
        self._retries               = retries
        self._request_retries       = request_retries
        self.flood_sleep_threshold  = flood_sleep_threshold
        self._flood_waited_requests: dict = {}

        self._inited    = False
        self._connected = False

        # Entity cache (LRU + TTL)
        self._entity_cache = EntityCache(
            ttl=entity_cache_ttl, max_size=entity_cache_size
        )

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loggers = {
            name: logging.getLogger(name)
            for name in (
                __name__,
                'tglib.network.mtprotosender',
                'tglib.network.mtprotostate',
                'tglib.network.connection',
            )
        }

    # ═══════════════════════════════════════════════════════════════════════
    #  Connection
    # ═══════════════════════════════════════════════════════════════════════

    async def connect(self):
        """Open a connection to Telegram."""
        if self._connected:
            return

        if self._updates_queue is None:
            self._updates_queue = asyncio.Queue()

        conn = make_connection(
            self._dc_id, test=self._test_mode, loggers=self._loggers
        )

        async def _on_auth_key(key: AuthKey):
            self.session.auth_key = key
            self.session.save()

        self._sender = MTProtoSender(
            auth_key=self.session.auth_key,
            loggers=self._loggers,
            retries=self._retries,
            auto_reconnect=self._auto_reconnect,
            auth_key_callback=_on_auth_key,
            updates_queue=self._updates_queue,
        )
        await self._sender.connect(conn)
        self._connected = True
        self._inited    = False
        __log__.info('Connected to DC %d', self._dc_id)
        self._updates_task = asyncio.ensure_future(self._run_update_loop())

    async def disconnect(self):
        """Close the connection gracefully."""
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

    async def is_connected(self) -> bool:
        return self._connected

    @property
    def _self_id(self) -> Optional[int]:
        return self._entity_cache.self_id

    # ═══════════════════════════════════════════════════════════════════════
    #  Raw API invoke (Telethon-compatible retry / flood / DC-migrate)
    # ═══════════════════════════════════════════════════════════════════════

    async def __call__(self, request, ordered: bool = False,
                       flood_sleep_threshold: int = None):
        """
        Invoke a raw TL function and return its result.

        Automatically:
          - Wraps the first call in invokeWithLayer(initConnection(…))
          - Handles DC migration (PHONE_MIGRATE_X, NETWORK_MIGRATE_X, …)
          - Sleeps on FloodWaitError up to flood_sleep_threshold seconds
          - Retries on transient server errors (SERVER_ERROR, RPC_CALL_FAIL, …)
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

        # Respect pending flood waits
        cid = getattr(getattr(request, 'query', request), 'CONSTRUCTOR_ID', None)
        if cid and cid in self._flood_waited_requests:
            due  = self._flood_waited_requests[cid]
            diff = round(due - time.time())
            if diff <= 3:
                self._flood_waited_requests.pop(cid, None)
            elif diff <= flood_sleep_threshold:
                __log__.info('Pre-sleeping %ds (flood) for %s',
                             diff, type(request).__name__)
                await asyncio.sleep(diff)
                self._flood_waited_requests.pop(cid, None)
            else:
                raise FloodWaitError(request=request, capture=diff)

        last_error = None
        for _ in range(self._request_retries):
            try:
                result = await self._sender.send(request, ordered=ordered)
                self._process_entities(result)
                return result

            except RPCError as e:
                m = re.match(
                    r'(?:PHONE|NETWORK|USER)_MIGRATE_(\d+)', e.message or '')
                if m:
                    await self._migrate_to_dc(int(m.group(1)))
                    inner = getattr(request, 'query', request)
                    inner = getattr(inner, 'query', inner)
                    return await self(inner, ordered=ordered)
                raise

            except FloodWaitError as e:
                last_error = e
                if cid:
                    self._flood_waited_requests[cid] = time.time() + e.seconds
                secs = max(e.seconds, 1)
                if secs <= flood_sleep_threshold:
                    __log__.info('FloodWait %ds for %s',
                                 secs, type(request).__name__)
                    await asyncio.sleep(secs)
                else:
                    raise

            except Exception as e:
                last_error = e
                msg = str(e)
                if any(x in msg for x in ('SERVER_ERROR', 'RPC_CALL_FAIL',
                                           'RPC_MCGET_FAIL', 'INTER_DC_CALL')):
                    __log__.warning('Transient error (%s), retrying…', msg)
                    await asyncio.sleep(2)
                else:
                    raise

        raise last_error or RuntimeError(
            f'Request failed after {self._request_retries} attempts')

    def _process_entities(self, result):
        """Populate entity cache from API result."""
        if result is None:
            return
        users = getattr(result, 'users', []) or []
        chats = getattr(result, 'chats', []) or []
        if users or chats:
            self._entity_cache.extend(users, chats)
        try:
            self.session.process_entities(result)
        except Exception:
            pass

    async def _migrate_to_dc(self, dc_id: int):
        from .network.connection import DC_MAP
        if self._sender:
            await self._sender.disconnect()

        self._dc_id = dc_id
        ip, port = DC_MAP.get(dc_id, ('149.154.167.51', 443))
        self.session.set_dc(dc_id, ip, port)
        self.session.auth_key = AuthKey(None)
        self.session.save()

        conn = make_connection(dc_id, test=self._test_mode,
                               loggers=self._loggers)

        async def _on_auth_key(key: AuthKey):
            self.session.auth_key = key
            self.session.save()

        self._sender = MTProtoSender(
            auth_key=self.session.auth_key,
            loggers=self._loggers,
            retries=self._retries,
            auto_reconnect=self._auto_reconnect,
            auth_key_callback=_on_auth_key,
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
        self._updates_task = asyncio.ensure_future(self._run_update_loop())

    # ═══════════════════════════════════════════════════════════════════════
    #  Auth
    # ═══════════════════════════════════════════════════════════════════════

    async def send_code_request(self, phone: str):
        """Send a login code to *phone*. Returns ``auth.SentCode``."""
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
        """Sign in with code or 2FA password."""
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
        from .tl.functions.auth import CheckPasswordRequest
        from .password import compute_srp_answer
        pwd = await self(GetPasswordRequest())
        try:
            return await self(CheckPasswordRequest(
                password=await compute_srp_answer(pwd, password)
            ))
        except PasswordHashInvalidError:
            raise PasswordHashInvalidError() from None

    async def sign_in_bot(self, bot_token: str):
        """Sign in as a bot using *bot_token*."""
        from .tl.functions.auth import ImportBotAuthorizationRequest
        return await self(ImportBotAuthorizationRequest(
            flags=0,
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_auth_token=bot_token,
        ))

    async def sign_up(self, phone: str, phone_code_hash: str, code: str,
                      first_name: str, last_name: str = ''):
        """Register a new account (for new phone numbers)."""
        from .tl.functions.auth import SignUpRequest
        return await self(SignUpRequest(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code,
            first_name=first_name,
            last_name=last_name,
        ))

    async def log_out(self):
        """Terminate the current session and clear auth key."""
        from .tl.functions.auth import LogOutRequest
        try:
            await self(LogOutRequest())
        except Exception:
            pass
        self.session.auth_key = AuthKey(None)
        self.session.save()

    async def is_user_authorized(self) -> bool:
        """Return True if the current session is authorised."""
        try:
            me = await self.get_me()
            return me is not None
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════════════
    #  Entity resolution (Telethon-compatible priority chain)
    # ═══════════════════════════════════════════════════════════════════════

    async def get_me(self):
        """Return the ``User`` object for the currently logged-in account."""
        from .tl.functions.users import GetUsersRequest
        from .tl.types import InputUserSelf
        result = await self(GetUsersRequest(id=[InputUserSelf()]))
        user = result[0] if result else None
        if user and not self._entity_cache.self_id:
            self._entity_cache.set_self_user(
                user.id,
                getattr(user, 'bot', False),
                getattr(user, 'access_hash', 0) or 0,
            )
        return user

    async def get_entity(self, entity):
        """
        Resolve *entity* to a full ``User / Chat / Channel`` object.

        Accepts: 'me', '@username', '+phone', integer ID,
        Peer / InputPeer objects, or full entity objects.
        """
        from .tl.functions.users import GetUsersRequest
        from .tl.functions.channels import GetChannelsRequest
        from .tl.functions.messages import GetChatsRequest
        from .tl.functions.contacts import ResolveUsernameRequest
        from .tl.types import (
            InputUserSelf, InputUser, InputChannel,
            PeerUser, PeerChat, PeerChannel,
        )

        if isinstance(entity, str):
            if entity.lower() in ('me', 'self'):
                return await self.get_me()
            if entity.startswith('+'):
                row = self.session.get_entity_rows_by_phone(entity)
                if row:
                    return await self._get_user_by_id(row[0], row[1])
                raise ValueError(f'No entity for phone {entity}')
            username = entity.lstrip('@')
            result   = await self(ResolveUsernameRequest(username=username))
            self._process_entities(result)
            if getattr(result, 'users', None):
                return result.users[0]
            if getattr(result, 'chats', None):
                return result.chats[0]
            raise ValueError(f'No entity for username {username!r}')

        if isinstance(entity, int):
            return await self._get_entity_by_int_id(entity)

        if isinstance(entity, PeerUser):
            return await self._get_user_by_id(entity.user_id)
        if isinstance(entity, PeerChat):
            result = await self(GetChatsRequest(id=[entity.chat_id]))
            return result.chats[0] if result.chats else None
        if isinstance(entity, PeerChannel):
            cached = self._entity_cache.get(entity.channel_id)
            ah = cached.hash if cached else 0
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=entity.channel_id, access_hash=ah)
            ]))
            self._process_entities(result)
            return result.chats[0] if result.chats else None

        return entity  # already a full entity

    async def _get_entity_by_int_id(self, entity_id: int):
        abs_id = abs(entity_id)
        cached = self._entity_cache.get(abs_id)
        if cached:
            ip = cached._as_input_peer()
            return await self.get_entity(ip)
        row = self.session.get_entity_rows_by_id(abs_id)
        if row:
            return await self._get_user_by_id(row[0], row[1])
        try:
            return await self._get_user_by_id(abs_id, 0)
        except Exception:
            pass
        try:
            from .tl.functions.channels import GetChannelsRequest
            from .tl.types import InputChannel
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=abs_id, access_hash=0)
            ]))
            self._process_entities(result)
            if result.chats:
                return result.chats[0]
        except Exception:
            pass
        raise ValueError(f'Could not find entity with ID {entity_id}')

    async def _get_user_by_id(self, user_id: int, access_hash: int = 0):
        from .tl.functions.users import GetUsersRequest
        from .tl.types import InputUser
        result = await self(GetUsersRequest(id=[
            InputUser(user_id=user_id, access_hash=access_hash)
        ]))
        if result:
            self._entity_cache.extend(result, [])
            return result[0]
        return None

    async def _get_channel_by_id(self, channel_id: int, access_hash: int = 0):
        from .tl.functions.channels import GetChannelsRequest
        from .tl.types import InputChannel
        try:
            result = await self(GetChannelsRequest(id=[
                InputChannel(channel_id=channel_id, access_hash=access_hash)
            ]))
            self._process_entities(result)
            chats = getattr(result, 'chats', [])
            return chats[0] if chats else None
        except Exception:
            return None

    async def get_input_entity(self, peer):
        """
        Resolve *peer* to an ``InputPeer``.

        Priority:
          1. Already InputPeer / 'me' / 'self'
          2. In-memory EntityCache (O(1))
          3. SQLite session cache
          4. Network (ResolveUsername / GetUsers / GetChannels)
        """
        from .tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel,
            InputPeerSelf, InputPeerEmpty,
            PeerUser, PeerChat, PeerChannel,
        )

        if isinstance(peer, str) and peer.lower() in ('me', 'self'):
            return InputPeerSelf()
        if isinstance(peer, (InputPeerUser, InputPeerChat,
                              InputPeerChannel, InputPeerSelf, InputPeerEmpty)):
            return peer

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
            u = await self._get_user_by_id(uid, 0)
            if u:
                return InputPeerUser(
                    user_id=u.id,
                    access_hash=getattr(u, 'access_hash', 0) or 0)
            return InputPeerUser(user_id=uid, access_hash=0)

        if isinstance(peer, PeerChat):
            return InputPeerChat(chat_id=peer.chat_id)

        if isinstance(peer, PeerChannel):
            cid    = peer.channel_id
            cached = self._entity_cache.get(cid)
            if cached:
                return cached._as_input_peer()
            row = self.session.get_entity_rows_by_id(cid)
            if row:
                return InputPeerChannel(channel_id=row[0], access_hash=row[1])
            c = await self._get_channel_by_id(cid, 0)
            if c:
                return InputPeerChannel(
                    channel_id=c.id,
                    access_hash=getattr(c, 'access_hash', 0) or 0)
            return InputPeerChannel(channel_id=cid, access_hash=0)

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
                entity = await self.get_entity(peer)
                return await self.get_input_entity(entity)

        if isinstance(peer, int):
            # Decode the marked peer ID into (raw_id, peer_type).
            # Marked format: user → positive; chat → -chat_id; channel → -100XXXX
            raw_id, peer_cls = utils.resolve_id(peer)

            # Fast-path: this is our own user ID
            if self._self_id and peer_cls is PeerUser and raw_id == self._self_id:
                return InputPeerSelf()

            if peer_cls is PeerUser:
                if raw_id == (self._self_id or -1):
                    return InputPeerSelf()
                cached = self._entity_cache.get(raw_id)
                if cached:
                    return cached._as_input_peer()
                row = self.session.get_entity_rows_by_id(raw_id)
                if row:
                    return InputPeerUser(user_id=row[0], access_hash=row[1])
                u = await self._get_user_by_id(raw_id, 0)
                if u:
                    return InputPeerUser(user_id=u.id,
                                        access_hash=getattr(u, 'access_hash', 0) or 0)

            elif peer_cls is PeerChat:
                return InputPeerChat(chat_id=raw_id)

            elif peer_cls is PeerChannel:
                cached = self._entity_cache.get(raw_id)
                if cached:
                    return cached._as_input_peer()
                row = self.session.get_entity_rows_by_id(raw_id)
                if row:
                    return InputPeerChannel(channel_id=row[0], access_hash=row[1])
                c = await self._get_channel_by_id(raw_id, 0)
                if c:
                    return InputPeerChannel(
                        channel_id=c.id,
                        access_hash=getattr(c, 'access_hash', 0) or 0)

            raise ValueError(
                f'Cannot find input entity for ID {peer}. '
                'Make sure you have interacted with this entity first.'
            )

        # Full entity objects
        eid   = getattr(peer, 'id', None)
        ehash = getattr(peer, 'access_hash', 0) or 0
        cls   = type(peer).__name__.lower()
        if eid is not None:
            if 'user' in cls:
                if eid == self._self_id:
                    return InputPeerSelf()
                return InputPeerUser(user_id=eid, access_hash=ehash)
            elif 'chat' in cls and 'channel' not in cls:
                return InputPeerChat(chat_id=eid)
            elif any(x in cls for x in ('channel', 'supergroup', 'megagroup')):
                return InputPeerChannel(channel_id=eid, access_hash=ehash)

        raise ValueError(
            f'Cannot find input entity for {peer!r} ({type(peer).__name__}). '
            'See https://docs.telethon.dev/en/stable/concepts/entities.html'
        )

    async def get_peer_id(self, peer, add_mark: bool = True) -> int:
        """Return the marked integer ID for *peer*."""
        ip = await self.get_input_entity(peer)
        from .tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel, InputPeerSelf
        )
        if isinstance(ip, InputPeerSelf):
            return self._self_id or (await self.get_me()).id
        if isinstance(ip, InputPeerUser):
            return ip.user_id
        if isinstance(ip, InputPeerChat):
            return -ip.chat_id if add_mark else ip.chat_id
        if isinstance(ip, InputPeerChannel):
            return int(f'-100{ip.channel_id}') if add_mark else ip.channel_id
        raise ValueError(f'Cannot get peer_id from {ip!r}')

    # ═══════════════════════════════════════════════════════════════════════
    #  Text formatting
    # ═══════════════════════════════════════════════════════════════════════

    def _get_response_message(self, request, result, input_chat):
        """
        Extract a single Message object from an API result.

        Telegram returns Updates / UpdatesCombined for messages sent to
        channels, and UpdateShortSentMessage for self-DMs.  This method
        unpacks the inner updates and returns the concrete Message,
        mirroring Telethon's messageparse._get_response_message().

        Parameters
        ----------
        request : TLObject | int
            The original request (must have .random_id) OR a bare random_id int.
        result  : TLObject
            The raw API response.
        input_chat : InputPeer
            The resolved peer (used for context, not strictly needed here).

        Returns
        -------
        types.Message | None
        """
        from .tl.types import (
            UpdateShort, Updates, UpdatesCombined,
            UpdateShortSentMessage, UpdateMessageId,
            UpdateNewMessage, UpdateNewChannelMessage,
            UpdateEditMessage, UpdateEditChannelMessage,
        )

        if isinstance(result, UpdateShortSentMessage):
            # Sent to Saved Messages (self): reconstruct a stub message.
            # The actual full message will arrive as UpdateNewMessage shortly.
            from .tl.types import Message, PeerUser
            return Message(
                id=result.id,
                peer_id=PeerUser(user_id=self._self_id or 0),
                from_id=PeerUser(user_id=self._self_id or 0),
                message=getattr(request, 'message', ''),
                date=result.date,
                out=True,
            )

        if isinstance(result, UpdateShort):
            updates = [result.update]
            entities = {}
        elif isinstance(result, (Updates, UpdatesCombined)):
            updates = result.updates or []
            entities = {
                utils.get_peer_id(x): x
                for x in list(result.users or []) + list(result.chats or [])
            }
        else:
            # Already a concrete object (rare): return as-is
            return result

        # Map random_id → message_id via UpdateMessageID
        random_id = (request if isinstance(request, int)
                     else getattr(request, 'random_id', None))
        random_to_id = {}
        id_to_msg    = {}

        for upd in updates:
            if isinstance(upd, UpdateMessageId):
                random_to_id[upd.random_id] = upd.id

            elif isinstance(upd, (UpdateNewMessage, UpdateNewChannelMessage)):
                msg = upd.message
                id_to_msg[msg.id] = msg

            elif isinstance(upd, (UpdateEditMessage, UpdateEditChannelMessage)):
                msg = upd.message
                # For edits, match by message id from the original request
                req_id = getattr(request, 'id', None)
                if req_id is not None and msg.id == req_id:
                    return msg
                id_to_msg[msg.id] = msg

        if random_id is not None and random_id in random_to_id:
            mid = random_to_id[random_id]
            if mid in id_to_msg:
                return id_to_msg[mid]

        # Fallback: return the first message we found (or None)
        return next(iter(id_to_msg.values()), None)

    def _parse_text(self, text: str, parse_mode: str = None):
        """Return (plain_text, entity_list) for *text* with the given mode."""
        if not text:
            return text, []
        mode = utils.sanitize_parse_mode(parse_mode)
        if mode == 'md':
            from .extensions.markdown import parse
            return parse(text)
        if mode == 'html':
            from .extensions.html import parse
            return parse(text)
        return text, []

    # ═══════════════════════════════════════════════════════════════════════
    #  Messaging
    # ═══════════════════════════════════════════════════════════════════════

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
        buttons=None,
        file=None,
    ):
        """
        Send a text message (or media+caption via *file*) to *entity*.

        Parameters
        ----------
        entity : peer
            Destination chat / user.
        message : str
            The text to send (supports Markdown / HTML via *parse_mode*).
        reply_to : int | Message, optional
            Reply to this message ID.
        parse_mode : str, optional
            ``'md'`` / ``'markdown'`` or ``'html'``.
        link_preview : bool
            Whether to generate a link preview (default True).
        silent : bool
            Send without notification sound.
        clear_draft : bool
            Clear the chat draft on send.
        schedule_date : datetime, optional
            Schedule the message for a future time.
        buttons : list, optional
            Inline keyboard / reply keyboard buttons (bot-only).
        file : path | bytes | file-like, optional
            If provided, sends as a media message instead.
        """
        if file is not None:
            return await self.send_file(
                entity, file, caption=message,
                reply_to=reply_to, parse_mode=parse_mode,
                silent=silent, clear_draft=clear_draft,
                schedule=schedule_date,
            )

        from .tl.functions.messages import SendMessageRequest
        from .tl.types import InputReplyToMessage

        peer = await self.get_input_entity(entity)
        text, entities = self._parse_text(message, parse_mode)

        kwargs: dict = dict(
            peer=peer,
            message=text,
            random_id=helpers.generate_random_long(),
            no_webpage=not link_preview,
            silent=silent,
            clear_draft=clear_draft,
            entities=entities or None,
        )
        if reply_to is not None:
            rid = utils.get_message_id(reply_to)
            if rid:
                kwargs['reply_to'] = InputReplyToMessage(reply_to_msg_id=rid)
        if schedule_date:
            kwargs['schedule_date'] = schedule_date

        result = await self(SendMessageRequest(**kwargs))
        return self._get_response_message(kwargs['random_id'], result, peer)

    async def edit_message(
        self,
        entity,
        message_id: int,
        text: str,
        *,
        parse_mode: str = None,
        link_preview: bool = True,
        buttons=None,
        file=None,
    ):
        """Edit an existing message."""
        from .tl.functions.messages import EditMessageRequest

        peer = await self.get_input_entity(entity)
        msg, entities = self._parse_text(text, parse_mode)
        request = EditMessageRequest(
            peer=peer,
            id=message_id,
            message=msg,
            no_webpage=not link_preview,
            entities=entities or None,
            media=None,
        )
        result = await self(request)
        extracted = self._get_response_message(request, result, peer)
        return extracted if extracted is not None else result

    async def delete_messages(self, entity, message_ids, *,
                              revoke: bool = True):
        """
        Delete messages by ID.

        Parameters
        ----------
        entity : peer
            The chat.  For non-channel chats you may pass None.
        message_ids : int | list of int
            IDs to delete.
        revoke : bool
            If True (default), delete for everyone; otherwise just for you.
        """
        from .tl.functions.messages import DeleteMessagesRequest
        from .tl.functions.channels import DeleteMessagesRequest as ChDeleteRequest

        if not isinstance(message_ids, (list, tuple)):
            message_ids = [message_ids]

        if entity is not None:
            peer = await self.get_input_entity(entity)
            from .tl.types import InputPeerChannel
            if isinstance(peer, InputPeerChannel):
                from .tl.types import InputChannel
                return await self(ChDeleteRequest(
                    channel=InputChannel(
                        channel_id=peer.channel_id,
                        access_hash=peer.access_hash,
                    ),
                    id=message_ids,
                ))

        return await self(DeleteMessagesRequest(id=message_ids, revoke=revoke))

    async def get_messages(self, entity, limit: int = 100, *,
                           offset_id: int = 0, min_id: int = 0,
                           max_id: int = 0, ids=None,
                           search: str = None, reverse: bool = False):
        """
        Fetch messages from *entity*.

        Returns a list of ``Message`` objects (newest first by default).
        For large ranges use :meth:`iter_messages`.
        """
        if ids is not None:
            return await self._get_messages_by_ids(entity, ids)

        items = []
        async for msg in self.iter_messages(
            entity, limit=limit, offset_id=offset_id,
            min_id=min_id, max_id=max_id,
            search=search, reverse=reverse,
        ):
            items.append(msg)
        return items

    async def _get_messages_by_ids(self, entity, ids):
        from .tl.functions.messages import GetMessagesRequest
        from .tl.functions.channels import GetMessagesRequest as ChGet
        from .tl.types import InputMessageID

        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        id_objs = [InputMessageID(i) for i in ids]

        if entity is not None:
            peer = await self.get_input_entity(entity)
            from .tl.types import InputPeerChannel, InputChannel
            if isinstance(peer, InputPeerChannel):
                result = await self(ChGet(
                    channel=InputChannel(
                        channel_id=peer.channel_id,
                        access_hash=peer.access_hash,
                    ),
                    id=id_objs,
                ))
                self._process_entities(result)
                return result.messages

        result = await self(GetMessagesRequest(id=id_objs))
        self._process_entities(result)
        return result.messages

    def iter_messages(
        self,
        entity,
        limit: int = None,
        *,
        offset_id: int    = 0,
        offset_date       = None,
        max_id: int       = 0,
        min_id: int       = 0,
        add_offset: int   = 0,
        search: str       = None,
        filter            = None,
        from_user         = None,
        wait_time: float  = None,
        ids               = None,
        reverse: bool     = False,
        reply_to: int     = None,
    ):
        """
        Iterate over messages from *entity* (newest first by default).

        Supports pagination, full-text search, filter, from_user, min/max ID,
        reverse ordering, reply thread iteration, and specific message IDs.

        Example::

            async for msg in client.iter_messages('me', limit=50):
                print(msg.message)

            # Search
            async for msg in client.iter_messages(chat, search='hello'):
                print(msg.id, msg.message)

            # Reverse (oldest first)
            async for msg in client.iter_messages(chat, limit=100, reverse=True):
                ...
        """
        from .tl.functions.messages import (
            GetHistoryRequest, SearchRequest, SearchGlobalRequest,
            GetRepliesRequest,
        )
        from .tl.types import InputMessagesFilterEmpty

        return _MessagesIter(
            self, limit,
            entity=entity,
            offset_id=offset_id,
            offset_date=offset_date,
            max_id=max_id,
            min_id=min_id,
            add_offset=add_offset,
            search=search,
            filter=filter,
            from_user=from_user,
            wait_time=wait_time,
            ids=ids,
            reverse=reverse,
            reply_to=reply_to,
        )

    async def search_messages(self, entity, query: str, *,
                              limit: int = 100, **kwargs):
        """Shorthand for iter_messages with a search query."""
        return await self.get_messages(
            entity, limit=limit, search=query, **kwargs)

    async def forward_messages(self, entity, message_ids, from_peer):
        """Forward messages to *entity* from *from_peer*."""
        from .tl.functions.messages import ForwardMessagesRequest

        to_peer    = await self.get_input_entity(entity)
        from_input = await self.get_input_entity(from_peer)
        if not isinstance(message_ids, list):
            message_ids = [message_ids]
        return await self(ForwardMessagesRequest(
            from_peer=from_input,
            id=message_ids,
            to_peer=to_peer,
            random_id=[helpers.generate_random_long() for _ in message_ids],
        ))

    async def pin_message(self, entity, message_id: int, *,
                          notify: bool = False, pm_oneside: bool = False):
        """Pin *message_id* in *entity*."""
        from .tl.functions.messages import UpdatePinnedMessageRequest
        peer = await self.get_input_entity(entity)
        return await self(UpdatePinnedMessageRequest(
            peer=peer, id=message_id, silent=not notify,
            pm_oneside=pm_oneside,
        ))

    async def unpin_message(self, entity, message_id: int):
        """Unpin *message_id* in *entity*."""
        from .tl.functions.messages import UpdatePinnedMessageRequest
        peer = await self.get_input_entity(entity)
        return await self(UpdatePinnedMessageRequest(
            peer=peer, id=message_id, unpin=True, silent=True,
        ))

    async def unpin_all_messages(self, entity):
        """Unpin all messages in *entity*."""
        from .tl.functions.messages import UnpinAllMessagesRequest
        peer = await self.get_input_entity(entity)
        return await self(UnpinAllMessagesRequest(peer=peer, top_msg_id=None))

    # ═══════════════════════════════════════════════════════════════════════
    #  Dialogs / Chats
    # ═══════════════════════════════════════════════════════════════════════

    async def get_dialogs(self, limit: int = 100):
        """Fetch the most recent dialogs."""
        from .tl.functions.messages import GetDialogsRequest
        from .tl.types import InputPeerEmpty
        result = await self(GetDialogsRequest(
            offset_date=0, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=limit, hash=0,
        ))
        self._process_entities(result)
        return result

    def iter_dialogs(self, limit: int = None):
        """
        Asynchronous iterator over all dialogs (newest first).

        Example::

            async for dialog in client.iter_dialogs():
                print(dialog.name)
        """
        return _DialogsIter(self, limit)

    async def get_participants(self, entity, limit: int = 200, *,
                               search: str = '', aggressive: bool = False):
        """Fetch participants of a channel / supergroup."""
        from .tl.functions.channels import GetParticipantsRequest
        from .tl.types import (
            ChannelParticipantsRecent, ChannelParticipantsSearch,
            InputChannel,
        )
        peer = await self.get_input_entity(entity)
        if not hasattr(peer, 'channel_id'):
            raise ValueError('Entity is not a channel / supergroup')
        channel = InputChannel(
            channel_id=peer.channel_id, access_hash=peer.access_hash
        )
        filter_ = (ChannelParticipantsSearch(q=search)
                   if search else ChannelParticipantsRecent())
        return await self(GetParticipantsRequest(
            channel=channel, filter=filter_,
            offset=0, limit=limit, hash=0,
        ))

    def iter_participants(self, entity, limit: int = None, *, search: str = ''):
        """
        Asynchronous iterator over channel / supergroup participants.

        Example::

            async for user in client.iter_participants(chat, search='John'):
                print(user.first_name)
        """
        return _ParticipantsIter(self, limit, entity=entity, search=search)

    # ═══════════════════════════════════════════════════════════════════════
    #  Upload (ported from Telethon uploads.py)
    # ═══════════════════════════════════════════════════════════════════════

    async def upload_file(
        self,
        file,
        *,
        part_size_kb:      float = None,
        file_size:         int   = None,
        file_name:         str   = None,
        progress_callback: Callable = None,
        key:  bytes = None,
        iv:   bytes = None,
    ):
        """
        Upload *file* to Telegram's servers and return an ``InputFile`` handle.

        The handle expires within ~24 hours.  You normally want :meth:`send_file`
        instead.

        Parameters
        ----------
        file : str | bytes | file-like
            Local path, raw bytes, or any readable stream.
        part_size_kb : float, optional
            Upload chunk size.  Determined automatically if not set.
        file_size : int, optional
            Total file size.  Determined automatically if not set.
        file_name : str, optional
            Override the filename used in Telegram.
        progress_callback : callable, optional
            ``(sent_bytes, total_bytes)`` callback.
        key, iv : bytes, optional
            AES-IGE key and IV for encrypted uploads (secret chats).
        """
        from .tl.functions.upload import SaveFilePartRequest, SaveBigFilePartRequest
        from .tl.types import InputFile, InputFileBig

        async with helpers._FileStream(file, file_size=file_size) as stream:
            file_size = stream.file_size

            if not part_size_kb:
                part_size_kb = utils.get_appropriated_part_size(file_size)
            if part_size_kb > 512:
                raise ValueError('part_size_kb must be ≤ 512')

            part_size = int(part_size_kb * 1024)
            if part_size % 1024:
                raise ValueError('part_size must be divisible by 1024')

            file_id   = helpers.generate_random_long()
            if not file_name:
                file_name = stream.name or str(file_id)
            if not os.path.splitext(file_name)[-1]:
                file_name += utils._get_extension(stream)

            is_big    = file_size > BIG_FILE_THRESHOLD
            hash_md5  = hashlib.md5()
            part_count = (file_size + part_size - 1) // part_size

            __log__.info('Uploading %d bytes in %d chunks of %d B',
                         file_size, part_count, part_size)
            pos = 0

            for part_index in range(part_count):
                part = await helpers._maybe_await(stream.read(part_size))
                if not isinstance(part, bytes):
                    raise TypeError(
                        f'stream.read() returned {type(part).__name__}, expected bytes')

                pos += len(part)
                if key and iv:
                    from .crypto.aes import AES
                    part = AES.encrypt_ige(part, key, iv)

                if not is_big:
                    hash_md5.update(part)

                if is_big:
                    req = SaveBigFilePartRequest(file_id, part_index, part_count, part)
                else:
                    req = SaveFilePartRequest(file_id, part_index, part)

                ok = await self(req)
                if not ok:
                    raise RuntimeError(f'Failed to upload part {part_index}')

                __log__.debug('Uploaded part %d/%d', part_index + 1, part_count)
                if progress_callback:
                    await helpers._maybe_await(progress_callback(pos, file_size))

        if is_big:
            return InputFileBig(file_id, part_count, file_name)
        return InputFile(file_id, part_count, file_name, hash_md5.digest())

    async def send_file(
        self,
        entity,
        file,
        *,
        caption:            str   = '',
        force_document:     bool  = False,
        mime_type:          str   = None,
        file_size:          int   = None,
        progress_callback:  Callable = None,
        reply_to                  = None,
        attributes:         list  = None,
        thumb                     = None,
        voice_note:         bool  = False,
        video_note:         bool  = False,
        silent:             bool  = None,
        supports_streaming: bool  = False,
        schedule                  = None,
        clear_draft:        bool  = False,
        parse_mode:         str   = None,
        ttl:                int   = None,
        nosound_video:      bool  = None,
    ):
        """
        Send *file* to *entity* with an optional *caption*.

        Supports local paths, raw bytes, URLs, previously-uploaded handles,
        and album sending (pass a list for *file*).

        Parameters
        ----------
        entity : peer
            Recipient chat / user.
        file : str | bytes | file-like | list
            A single file or a list for album sending.
        caption : str, optional
            Caption text (supports *parse_mode*).
        force_document : bool
            Force sending as a raw document even if it looks like a photo/video.
        progress_callback : callable, optional
            ``(sent_bytes, total_bytes)`` callback.
        reply_to : int | Message, optional
            Reply to this message.
        voice_note : bool
            Send audio as a voice note.
        video_note : bool
            Send video as a round video message.
        supports_streaming : bool
            Mark video as streamable.
        ttl : int, optional
            Self-destruct timer in seconds (1-60).
        schedule : datetime, optional
            Schedule the send.
        """
        from .tl.functions.messages import SendMediaRequest
        from .tl.types import InputReplyToMessage

        peer    = await self.get_input_entity(entity)
        caption, caption_entities = self._parse_text(caption or '', parse_mode)
        reply_id = utils.get_message_id(reply_to)

        # Album sending
        if utils.is_list_like(file):
            return await self._send_album(
                peer, list(file),
                caption=caption, caption_entities=caption_entities,
                progress_callback=progress_callback,
                reply_to=reply_id,
                silent=silent, schedule=schedule,
                clear_draft=clear_draft,
                force_document=force_document,
                supports_streaming=supports_streaming,
                ttl=ttl,
            )

        file_handle, media, is_image = await self._file_to_media(
            file,
            force_document=force_document,
            mime_type=mime_type,
            file_size=file_size,
            progress_callback=progress_callback,
            attributes=attributes,
            thumb=thumb,
            voice_note=voice_note,
            video_note=video_note,
            supports_streaming=supports_streaming,
            ttl=ttl,
            nosound_video=nosound_video,
        )
        if not media:
            raise TypeError(f'Cannot use {file!r} as file')

        reply_to_obj = (InputReplyToMessage(reply_to_msg_id=reply_id)
                        if reply_id else None)

        return await self(SendMediaRequest(
            peer=peer,
            media=media,
            reply_to=reply_to_obj,
            message=caption,
            entities=caption_entities or None,
            silent=silent,
            schedule_date=schedule,
            clear_draft=clear_draft,
            random_id=helpers.generate_random_long(),
        ))

    async def _send_album(self, peer, files, *, caption='',
                          caption_entities=None, progress_callback=None,
                          reply_to=None, silent=None, schedule=None,
                          clear_draft=False, force_document=False,
                          supports_streaming=False, ttl=None):
        """Internal: send multiple files as a media album."""
        from .tl.functions.messages import (
            SendMultiMediaRequest, UploadMediaRequest,
        )
        from .tl.types import (
            InputSingleMedia, InputReplyToMessage,
            InputMediaUploadedPhoto, InputMediaPhotoExternal,
            InputMediaUploadedDocument, InputMediaDocumentExternal,
        )

        sent = 0
        cb   = None
        if progress_callback:
            cb = lambda s, t: progress_callback(sent + s, len(files))

        media_list = []
        for i, f in enumerate(files):
            sent = i
            _, fm, _ = await self._file_to_media(
                f, force_document=force_document, ttl=ttl,
                progress_callback=cb, nosound_video=True,
                supports_streaming=supports_streaming,
            )
            if isinstance(fm, (InputMediaUploadedPhoto, InputMediaPhotoExternal)):
                r  = await self(UploadMediaRequest(peer=peer, media=fm))
                fm = utils.get_input_media(r.photo)
            elif isinstance(fm, (InputMediaUploadedDocument,
                                  InputMediaDocumentExternal)):
                r  = await self(UploadMediaRequest(peer=peer, media=fm))
                fm = utils.get_input_media(r.document,
                                            supports_streaming=supports_streaming)

            c = caption if i == 0 else ''
            e = caption_entities if i == 0 else None
            media_list.append(InputSingleMedia(
                media=fm,
                message=c,
                entities=e,
            ))

        result = await self(SendMultiMediaRequest(
            peer=peer,
            reply_to=InputReplyToMessage(reply_to_msg_id=reply_to) if reply_to else None,
            multi_media=media_list,
            silent=silent,
            schedule_date=schedule,
            clear_draft=clear_draft,
        ))
        return result

    async def _file_to_media(
        self,
        file,
        force_document=False,
        mime_type=None,
        file_size=None,
        progress_callback=None,
        attributes=None,
        thumb=None,
        voice_note=False,
        video_note=False,
        supports_streaming=False,
        ttl=None,
        nosound_video=None,
        as_image=None,
    ):
        """Convert *file* to an InputMedia* object for sending."""
        from .tl.types import (
            InputFile, InputFileBig,
            InputMediaUploadedPhoto, InputMediaUploadedDocument,
            InputMediaPhotoExternal, InputMediaDocumentExternal,
        )

        if not file:
            return None, None, None

        if isinstance(file, pathlib.Path):
            file = str(file.absolute())

        is_image_ = utils.is_image(file)
        if as_image is None:
            as_image = is_image_ and not force_document

        if not isinstance(file, (str, bytes, InputFile, InputFileBig)) \
                and not hasattr(file, 'read'):
            try:
                return None, utils.get_input_media(
                    file, is_photo=as_image, attributes=attributes,
                    force_document=force_document, voice_note=voice_note,
                    video_note=video_note, supports_streaming=supports_streaming,
                    ttl=ttl,
                ), as_image
            except TypeError:
                return None, None, as_image

        file_handle = None
        media       = None

        if isinstance(file, (InputFile, InputFileBig)):
            file_handle = file
        elif isinstance(file, str) and re.match(r'https?://', file):
            if as_image:
                media = InputMediaPhotoExternal(url=file, ttl_seconds=ttl)
            else:
                media = InputMediaDocumentExternal(url=file, ttl_seconds=ttl)
        elif not isinstance(file, str) or os.path.isfile(file):
            file_handle = await self.upload_file(
                file, file_size=file_size, progress_callback=progress_callback
            )
        else:
            raise ValueError(
                f'Not a valid file, URL, or uploaded handle: {file!r}')

        if media:
            return file_handle, media, as_image

        if as_image:
            media = InputMediaUploadedPhoto(
                file=file_handle, ttl_seconds=ttl)
        else:
            attrs, mime = utils.get_attributes(
                file,
                mime_type=mime_type,
                attributes=attributes,
                force_document=force_document and not is_image_,
                voice_note=voice_note,
                video_note=video_note,
                supports_streaming=supports_streaming,
                thumb=thumb,
            )

            thumb_handle = None
            if thumb:
                thumb_handle = await self.upload_file(thumb)

            nv = nosound_video if (mime or '').startswith('video/') else None
            media = InputMediaUploadedDocument(
                file=file_handle,
                mime_type=mime,
                attributes=attrs,
                thumb=thumb_handle,
                force_file=force_document and not is_image_,
                ttl_seconds=ttl,
                nosound_video=nv,
            )

        return file_handle, media, as_image

    # ═══════════════════════════════════════════════════════════════════════
    #  Download (ported from Telethon downloads.py)
    # ═══════════════════════════════════════════════════════════════════════

    async def download_media(
        self,
        message,
        file=None,
        *,
        thumb=None,
        progress_callback: Callable = None,
    ):
        """
        Download media from *message* (or a media object directly).

        Parameters
        ----------
        message : Message | media
            A ``Message`` with media, or a raw media / document object.
        file : str | None | bytes, optional
            Destination path, directory, or ``bytes`` to download in-memory.
            If None, a filename is inferred from the media.
        thumb : int, optional
            Thumbnail index to download instead of the full media.
        progress_callback : callable, optional
            ``(downloaded_bytes, total_bytes)`` callback.

        Returns
        -------
        str | bytes | None
            Path to the downloaded file, raw bytes if ``file=bytes``,
            or None if no downloadable media was found.
        """
        import datetime
        from .tl import types

        msg_data = None
        if isinstance(message, types.Message):
            date     = message.date
            media    = message.media
            msg_data = (await self.get_input_entity(message.peer_id), message.id) \
                if hasattr(message, 'peer_id') else None
        else:
            date  = datetime.datetime.now()
            media = message

        if isinstance(media, types.MessageMediaWebPage):
            wp = getattr(media, 'webpage', None)
            if isinstance(wp, types.WebPage):
                media = wp.document or wp.photo

        if isinstance(media, (types.MessageMediaPhoto, types.Photo)):
            return await self._download_photo(media, file, date, thumb,
                                               progress_callback)
        elif isinstance(media, (types.MessageMediaDocument, types.Document)):
            return await self._download_document(media, file, date, thumb,
                                                  progress_callback, msg_data)
        return None

    async def _download_photo(self, photo, file, date, thumb, progress_cb):
        from .tl import types
        if isinstance(photo, types.MessageMediaPhoto):
            photo = photo.photo
        if not isinstance(photo, types.Photo):
            return None

        sizes = list(photo.sizes or [])
        if photo.video_sizes:
            sizes += list(photo.video_sizes)

        size_obj = self._pick_thumb(sizes, thumb)
        if not size_obj or isinstance(size_obj, types.PhotoSizeEmpty):
            return None

        if isinstance(size_obj, types.VideoSize):
            file = self._proper_filename(file, 'video', '.mp4', date=date)
        else:
            file = self._proper_filename(file, 'photo', '.jpg', date=date)

        if isinstance(size_obj, (types.PhotoCachedSize, types.PhotoStrippedSize)):
            return self._write_cached_photo(size_obj, file)

        total = (max(size_obj.sizes)
                 if isinstance(size_obj, types.PhotoSizeProgressive)
                 else size_obj.size)

        result = await self.download_file(
            types.InputPhotoFileLocation(
                id=photo.id,
                access_hash=photo.access_hash,
                file_reference=photo.file_reference,
                thumb_size=size_obj.type,
            ),
            file, file_size=total, progress_callback=progress_cb,
        )
        return result if file is bytes else file

    async def _download_document(self, document, file, date, thumb,
                                  progress_cb, msg_data):
        from .tl import types
        if isinstance(document, types.MessageMediaDocument):
            document = document.document
        if not isinstance(document, types.Document):
            return None

        if thumb is None:
            kind, names = self._doc_kind_and_names(document.attributes)
            file = self._proper_filename(
                file, kind, utils.get_extension(document),
                date=date, possible_names=names,
            )
            size_obj = None
        else:
            file     = self._proper_filename(file, 'photo', '.jpg', date=date)
            size_obj = self._pick_thumb(document.thumbs, thumb)
            if not size_obj or isinstance(size_obj, types.PhotoSizeEmpty):
                return None
            if isinstance(size_obj, (types.PhotoCachedSize, types.PhotoStrippedSize)):
                return self._write_cached_photo(size_obj, file)

        result = await self.download_file(
            types.InputDocumentFileLocation(
                id=document.id,
                access_hash=document.access_hash,
                file_reference=document.file_reference,
                thumb_size=size_obj.type if size_obj else '',
            ),
            file,
            file_size=size_obj.size if size_obj else document.size,
            progress_callback=progress_cb,
            msg_data=msg_data,
        )
        return result if file is bytes else file

    async def download_file(
        self,
        input_location,
        file=None,
        *,
        part_size_kb:      float    = None,
        file_size:         int      = None,
        dc_id:             int      = None,
        progress_callback: Callable = None,
        msg_data                    = None,
        key:  bytes = None,
        iv:   bytes = None,
    ):
        """
        Low-level download of *input_location* into *file*.

        Parameters
        ----------
        input_location : InputFileLocation
            The file location object.
        file : str | None | bytes
            Destination.  ``None`` or ``bytes`` → in-memory.
        part_size_kb : float, optional
            Download chunk size.
        file_size : int, optional
            Known total size (for progress reporting).
        dc_id : int, optional
            Override the DC to download from.
        progress_callback : callable, optional
            ``(downloaded, total)`` callback.
        """
        from .tl.functions.upload import GetFileRequest
        import inspect

        if not part_size_kb:
            part_size_kb = utils.get_appropriated_part_size(file_size) \
                if file_size else 64
        part_size = int(part_size_kb * 1024)
        if part_size % MIN_CHUNK_SIZE:
            raise ValueError('part_size must be divisible by 4096')

        if isinstance(file, pathlib.Path):
            file = str(file.absolute())

        in_memory = file is None or file is bytes
        if in_memory:
            f = io.BytesIO()
        elif isinstance(file, str):
            helpers.ensure_parent_dir_exists(file)
            f = open(file, 'wb')
        else:
            f = file

        try:
            offset = 0
            while True:
                req    = GetFileRequest(input_location,
                                        offset=offset, limit=part_size)
                result = await self(req)
                chunk  = result.bytes

                if key and iv:
                    from .crypto.aes import AES
                    chunk = AES.decrypt_ige(chunk, key, iv)

                if chunk:
                    f.write(chunk)
                    offset += len(chunk)
                    if progress_callback:
                        r = progress_callback(offset, file_size)
                        if inspect.isawaitable(r):
                            await r

                if len(chunk) < part_size:
                    break   # last chunk — we're done

            if callable(getattr(f, 'flush', None)):
                f.flush()
            if in_memory:
                return f.getvalue()
        finally:
            if isinstance(file, str) or in_memory:
                f.close()

    async def iter_download(
        self, file, *, offset: int = 0, chunk_size: int = MAX_CHUNK_SIZE
    ):
        """
        Async generator that yields raw bytes chunks of *file*.

        Useful for streaming large downloads without writing to disk::

            with open('video.mp4', 'wb') as fd:
                async for chunk in client.iter_download(media):
                    fd.write(chunk)
        """
        info = utils._get_file_info(file)
        loc  = info.location
        from .tl.functions.upload import GetFileRequest
        offset_ = offset
        while True:
            req    = GetFileRequest(loc, offset=offset_, limit=chunk_size)
            result = await self(req)
            chunk  = result.bytes
            if chunk:
                yield chunk
                offset_ += len(chunk)
            if len(chunk) < chunk_size:
                break

    async def download_profile_photo(self, entity, file=None, *,
                                      download_big: bool = True):
        """Download the profile photo of *entity*."""
        from .tl import types

        entity = await self.get_entity(entity)
        photo  = getattr(entity, 'photo', None)
        if not isinstance(photo, (types.UserProfilePhoto, types.ChatPhoto)):
            return None

        import datetime
        names = [getattr(entity, a, None)
                 for a in ('username', 'first_name', 'title')]
        file  = self._proper_filename(file, 'profile_photo', '.jpg',
                                       possible_names=[n for n in names if n])

        loc = types.InputPeerPhotoFileLocation(
            peer=await self.get_input_entity(entity),
            photo_id=photo.photo_id,
            big=download_big,
        )
        result = await self.download_file(loc, file, dc_id=photo.dc_id)
        return result if file is bytes else file

    # ── Download helpers ───────────────────────────────────────────────────

    @staticmethod
    def _pick_thumb(thumbs, thumb_spec):
        from .tl import types

        if not thumbs:
            return None

        def _sort_key(t):
            if isinstance(t, types.PhotoStrippedSize):
                return 1, len(t.bytes)
            if isinstance(t, types.PhotoCachedSize):
                return 1, len(t.bytes)
            if isinstance(t, types.PhotoSize):
                return 1, t.size
            if isinstance(t, types.PhotoSizeProgressive):
                return 1, max(t.sizes)
            if isinstance(t, types.VideoSize):
                return 2, t.size
            return 0, 0

        sorted_thumbs = sorted(thumbs, key=_sort_key)
        sorted_thumbs = [t for t in sorted_thumbs
                         if not isinstance(t, types.PhotoPathSize)]

        if thumb_spec is None:
            return sorted_thumbs[-1] if sorted_thumbs else None
        if isinstance(thumb_spec, int):
            return sorted_thumbs[thumb_spec]
        if isinstance(thumb_spec, str):
            return next((t for t in sorted_thumbs if t.type == thumb_spec), None)
        return thumb_spec

    @staticmethod
    def _doc_kind_and_names(attributes):
        from .tl import types
        kind  = 'document'
        names = []
        for attr in attributes:
            if isinstance(attr, types.DocumentAttributeFilename):
                names.insert(0, attr.file_name)
            elif isinstance(attr, types.DocumentAttributeAudio):
                kind = 'audio'
                if attr.performer and attr.title:
                    names.append(f'{attr.performer} - {attr.title}')
                elif attr.performer:
                    names.append(attr.performer)
                elif attr.title:
                    names.append(attr.title)
                if attr.voice:
                    kind = 'voice'
        return kind, names

    @staticmethod
    def _write_cached_photo(size, file):
        from .utils import stripped_photo_to_jpg
        from .tl import types
        data = (stripped_photo_to_jpg(size.bytes)
                if isinstance(size, types.PhotoStrippedSize)
                else size.bytes)
        if file is bytes:
            return data
        if isinstance(file, str):
            helpers.ensure_parent_dir_exists(file)
            with open(file, 'wb') as f:
                f.write(data)
            return file
        file.write(data)
        return file

    @staticmethod
    def _proper_filename(file, kind, extension, date=None, possible_names=None):
        import datetime as dt
        if isinstance(file, pathlib.Path):
            file = str(file.absolute())
        if file is not None and not isinstance(file, str):
            return file
        if file is None:
            file = ''
        elif os.path.isfile(file):
            return file

        if os.path.isdir(file) or not file:
            name = None
            if possible_names:
                for n in possible_names:
                    bn = os.path.basename(n) if n else None
                    if bn:
                        name = bn
                        break
            if not name:
                d = date or dt.datetime.now()
                name = '{}_{}_{:02}-{:02}-{:02}_{:02}-{:02}-{:02}'.format(
                    kind, d.year, d.month, d.day,
                    d.hour, d.minute, d.second,
                )
            file = os.path.join(file, name)

        directory, name = os.path.split(file)
        name, ext = os.path.splitext(name)
        if not ext:
            ext = extension
        result = os.path.join(directory, name + ext)
        if not os.path.isfile(result):
            return result
        i = 1
        while True:
            result = os.path.join(directory, f'{name} ({i}){ext}')
            if not os.path.isfile(result):
                return result
            i += 1

    # ═══════════════════════════════════════════════════════════════════════
    #  Event system
    # ═══════════════════════════════════════════════════════════════════════

    def on(self, event_builder):
        """
        Decorator to register an event handler.

        Usage::

            @client.on(events.NewMessage(pattern='hello'))
            async def greet(event):
                await event.reply('Hi!')

            # Raw updates (no filter)
            @client.on(events.Raw)
            async def raw(update):
                print(update)
        """
        def decorator(func):
            self.add_event_handler(func, event_builder)
            return func
        return decorator

    def add_event_handler(self, callback: Callable, event_builder=None):
        """
        Register *callback* as an event handler.

        Parameters
        ----------
        callback : coroutine function
            The handler to call.
        event_builder : EventBuilder class or instance, optional
            Filter.  If None, catches all updates (same as ``events.Raw``).
        """
        from .events.common import EventBuilder
        from .events.raw import Raw

        if event_builder is None:
            event_builder = Raw()
        elif isinstance(event_builder, type) and issubclass(event_builder, EventBuilder):
            event_builder = event_builder()

        self._event_handlers.append((event_builder, callback))

    def remove_event_handler(self, callback: Callable, event_builder=None):
        """Unregister a previously registered event handler."""
        self._event_handlers = [
            (eb, cb) for eb, cb in self._event_handlers
            if not (cb == callback and
                    (event_builder is None or type(eb) == type(event_builder)))
        ]

    def list_event_handlers(self) -> list:
        """Return a list of (event_builder, callback) pairs."""
        return list(self._event_handlers)

    # ═══════════════════════════════════════════════════════════════════════
    #  Update dispatcher (Telethon-compatible)
    # ═══════════════════════════════════════════════════════════════════════

    async def _run_update_loop(self):
        """Continuously pull updates from the queue and dispatch them."""
        while self._connected:
            try:
                update = await asyncio.wait_for(
                    self._updates_queue.get(), timeout=1.0)

                # Keep entity cache warm
                try:
                    self._process_entities(update)
                except Exception:
                    pass

                await self._dispatch_update(update)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                __log__.error('Update loop error: %s', e, exc_info=True)

    async def _dispatch_update(self, update):
        """Dispatch *update* to all matching event handlers.

        CRITICAL: Groups, channels and Saved Messages send their messages
        wrapped inside an ``Updates`` or ``UpdatesCombined`` container
        (constructor 0x74ae4240 / 0x725b04c3).  The container holds a list of
        individual update objects in its ``.updates`` field.  We MUST unwrap
        those containers first — otherwise NewMessage.build() never sees
        UpdateNewMessage / UpdateNewChannelMessage and all group / channel /
        saved-messages traffic is silently dropped.

        Private DMs arrive as UpdateShortMessage (0x9015e101) which is already
        a leaf update — no unwrapping needed — which is why DMs worked fine.
        """
        from .tl.types import Updates, UpdatesCombined

        # ── Unwrap containers ────────────────────────────────────────────────
        if isinstance(update, (Updates, UpdatesCombined)):
            # Warm the entity cache with users/chats embedded in the container
            try:
                self._process_entities(update)
            except Exception:
                pass

            # Recursively dispatch each inner update individually
            for inner in (update.updates or []):
                await self._dispatch_update(inner)
            return
        # ────────────────────────────────────────────────────────────────────

        from .events.common import EventBuilder

        for builder, handler in self._event_handlers:
            # Resolve lazy filters (username → ID, etc.) once
            if isinstance(builder, EventBuilder) and not builder.resolved:
                try:
                    await builder.resolve(self)
                except Exception as e:
                    __log__.warning('Could not resolve event filter: %s', e)

            # Build event object
            try:
                from .events.raw import Raw
                if isinstance(builder, Raw):
                    event = builder.filter(update)
                else:
                    event = builder.build(update,
                                          self_id=self._self_id)
                    if event is not None:
                        event = builder.filter(event)
            except Exception as e:
                __log__.warning('Event build/filter error: %s', e, exc_info=True)
                continue

            if event is None:
                continue

            # Attach client reference
            if hasattr(event, '_client'):
                event._client = self

            # Call the handler
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                __log__.error('Handler %s raised: %s', handler.__name__, e,
                               exc_info=True)

    async def run_until_disconnected(self):
        """Block until disconnected (Ctrl-C or ``await client.disconnect()``)."""
        try:
            while self._connected:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.disconnect()

    # ═══════════════════════════════════════════════════════════════════════
    #  Convenience / lifecycle
    # ═══════════════════════════════════════════════════════════════════════

    async def start(
        self,
        phone=None,
        password=None,
        bot_token=None,
        code_callback=None,
        first_name='New User',
        last_name='',
    ):
        """
        Authenticate the client interactively.

        Pass *bot_token* for bots, *phone* for userbots.
        *code_callback* can be a callable (sync or async) that returns the
        login code; defaults to ``input()``.

        Example::

            await client.start(phone='+1234567890')
            await client.start(bot_token='TOKEN:HERE')
        """
        await self.connect()
        if await self.is_user_authorized():
            return self

        if bot_token:
            await self.sign_in_bot(bot_token)
            return self

        if phone:
            sent = await self.send_code_request(phone)
            if code_callback:
                code = await helpers._maybe_await(code_callback())
            else:
                code = input('Enter the login code: ')
            try:
                await self.sign_in(phone, code,
                                   phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                pwd = password or input('Enter 2FA password: ')
                await self._sign_in_2fa(pwd)
        return self

    # ── Sync shims ────────────────────────────────────────────────────────

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def run(self, coro):
        """Run a coroutine synchronously (for scripts, not async contexts)."""
        return self._get_loop().run_until_complete(coro)

    # ── Context managers ──────────────────────────────────────────────────

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    def __enter__(self):
        self._get_loop().run_until_complete(self.start())
        return self

    def __exit__(self, *_):
        try:
            self._get_loop().run_until_complete(self.disconnect())
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
                self._loop = None

    def __repr__(self) -> str:
        return (f'TelegramClient(dc={self._dc_id}, '
                f'connected={self._connected}, '
                f'handlers={len(self._event_handlers)})')


# ══════════════════════════════════════════════════════════════════════════════
#  Async iterator implementations
# ══════════════════════════════════════════════════════════════════════════════

class _MessagesIter:
    """
    Async iterator for paginated message history / search.

    Ported and adapted from Telethon v1 MessageMethods._MessagesIter.
    Copyright (C) LonamiWebs — MIT License.
    """

    def __init__(self, client, limit, *, entity, offset_id=0, offset_date=None,
                 max_id=0, min_id=0, add_offset=0, search=None, filter=None,
                 from_user=None, wait_time=None, ids=None, reverse=False,
                 reply_to=None):
        self._client      = client
        self._limit       = limit
        self._entity      = entity
        self._offset_id   = offset_id
        self._offset_date = offset_date
        self._max_id      = max_id
        self._min_id      = min_id
        self._add_offset  = add_offset
        self._search      = search
        self._filter      = filter
        self._from_user   = from_user
        self._wait_time   = wait_time
        self._ids         = ids
        self._reverse     = reverse
        self._reply_to    = reply_to

        self._buffer: list = []
        self._total:  int  = 0
        self._inited: bool = False
        self._done:   bool = False
        self._request      = None
        self._left         = float('inf') if limit is None else max(0, limit)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._inited:
            await self._init()
            self._inited = True

        while not self._buffer:
            if self._done or self._left <= 0:
                raise StopAsyncIteration
            done = await self._load_chunk()
            if done:
                self._done = True
                if not self._buffer:
                    raise StopAsyncIteration

        msg = self._buffer.pop(0)
        self._left -= 1
        return msg

    async def _init(self):
        from .tl.functions.messages import (
            GetHistoryRequest, SearchRequest, SearchGlobalRequest,
            GetRepliesRequest,
        )
        from .tl.types import InputMessagesFilterEmpty, InputPeerEmpty

        if self._entity:
            self._peer = await self._client.get_input_entity(self._entity)
        else:
            self._peer = None

        if self._reverse:
            self._offset_id = max(self._offset_id, self._min_id)
            if self._offset_id:
                self._offset_id += 1
            elif not self._offset_date:
                self._offset_id = 1

        filt = self._filter
        if filt is None:
            filt = InputMessagesFilterEmpty()
        elif isinstance(filt, type):
            filt = filt()

        if self._reply_to is not None:
            self._request = GetRepliesRequest(
                peer=self._peer,
                msg_id=self._reply_to,
                offset_id=self._offset_id,
                offset_date=self._offset_date,
                add_offset=self._add_offset,
                limit=1, max_id=0, min_id=0, hash=0,
            )
        elif self._search is not None or not isinstance(filt, InputMessagesFilterEmpty):
            if self._peer:
                self._request = SearchRequest(
                    peer=self._peer, q=self._search or '',
                    filter=filt, min_date=None,
                    max_date=self._offset_date,
                    offset_id=self._offset_id,
                    add_offset=self._add_offset,
                    limit=1, max_id=0, min_id=0, hash=0,
                    from_id=None,
                )
            else:
                self._request = SearchGlobalRequest(
                    q=self._search or '', filter=filt,
                    min_date=None, max_date=self._offset_date,
                    offset_rate=0, offset_peer=InputPeerEmpty(),
                    offset_id=self._offset_id, limit=1,
                )
        else:
            self._request = GetHistoryRequest(
                peer=self._peer or InputPeerEmpty(),
                limit=1,
                offset_date=self._offset_date,
                offset_id=self._offset_id,
                min_id=0, max_id=0,
                add_offset=self._add_offset,
                hash=0,
            )

        if self._wait_time is None:
            self._wait_time = 1 if (self._limit or 0) > 3000 else 0

        if self._reverse and hasattr(self._request, 'add_offset'):
            self._request.add_offset -= _MAX_CHUNK_MESSAGES

    async def _load_chunk(self):
        if self._wait_time:
            await asyncio.sleep(self._wait_time)

        if hasattr(self._request, 'limit'):
            chunk_size = min(int(self._left), _MAX_CHUNK_MESSAGES) \
                if self._left != float('inf') else _MAX_CHUNK_MESSAGES
            self._request.limit = chunk_size

        r = await self._client(self._request)

        # Guard: if Telegram returns an unknown type (schema mismatch),
        # treat it as an empty result rather than crashing.
        from ..extensions.binaryreader import RawObject
        if isinstance(r, RawObject):
            __log__.warning(
                'iter_messages: server returned unknown type 0x%08x '
                '(schema mismatch) – stopping iteration',
                r.CONSTRUCTOR_ID,
            )
            self._total = 0
            return True

        self._total = getattr(r, 'count', len(r.messages))

        messages = list(reversed(r.messages)) if self._reverse else list(r.messages)

        for msg in messages:
            from .tl.types import MessageEmpty
            if isinstance(msg, MessageEmpty):
                continue
            if self._reverse:
                if msg.id <= (self._min_id or 0) or msg.id >= (self._max_id or float('inf')):
                    return True
            else:
                if (self._max_id and msg.id >= self._max_id) or \
                   (self._min_id and msg.id <= self._min_id):
                    return True
            self._buffer.append(msg)

        # Update offset for next page
        if self._buffer:
            last = self._buffer[-1]
            if hasattr(self._request, 'offset_id'):
                self._request.offset_id = last.id + (1 if self._reverse else 0)
            if hasattr(self._request, 'offset_date'):
                self._request.offset_date = last.date

        from .tl.types import messages as msgs_module
        if not r.messages or not hasattr(self._request, 'limit'):
            return True
        return False

    async def collect(self) -> list:
        return [msg async for msg in self]


class _DialogsIter:
    """Async iterator over dialogs (paginated)."""

    def __init__(self, client, limit):
        self._client = client
        self._limit  = limit
        self._buffer: list = []
        self._done   = False
        self._left   = float('inf') if limit is None else max(0, limit)
        self._offset_date = 0
        self._offset_id   = 0
        self._offset_peer = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._offset_peer:
            from .tl.types import InputPeerEmpty
            self._offset_peer = InputPeerEmpty()

        while not self._buffer:
            if self._done or self._left <= 0:
                raise StopAsyncIteration
            await self._load_chunk()

        item = self._buffer.pop(0)
        self._left -= 1
        return item

    async def _load_chunk(self):
        from .tl.functions.messages import GetDialogsRequest
        from .tl import types

        result = await self._client(GetDialogsRequest(
            offset_date=self._offset_date,
            offset_id=self._offset_id,
            offset_peer=self._offset_peer,
            limit=min(100, int(self._left) if self._left != float('inf') else 100),
            hash=0,
        ))
        self._client._process_entities(result)
        dialogs = getattr(result, 'dialogs', [])
        msgs    = {getattr(m, 'id', None): m
                   for m in getattr(result, 'messages', [])}

        self._buffer.extend(dialogs)

        if not dialogs or isinstance(result, types.messages.DialogsNotModified):
            self._done = True
            return

        # Compute next offset
        last = dialogs[-1]
        top_msg = msgs.get(getattr(last, 'top_message', None))
        if top_msg:
            self._offset_date = top_msg.date
            self._offset_id   = top_msg.id
        self._offset_peer = last.peer


class _ParticipantsIter:
    """Async iterator over channel participants."""

    def __init__(self, client, limit, *, entity, search=''):
        self._client  = client
        self._limit   = limit
        self._entity  = entity
        self._search  = search
        self._buffer: list = []
        self._offset  = 0
        self._done    = False
        self._left    = float('inf') if limit is None else max(0, limit)
        self._peer    = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._peer is None:
            self._peer = await self._client.get_input_entity(self._entity)

        while not self._buffer:
            if self._done or self._left <= 0:
                raise StopAsyncIteration
            await self._load_chunk()

        user = self._buffer.pop(0)
        self._left -= 1
        return user

    async def _load_chunk(self):
        from .tl.functions.channels import GetParticipantsRequest
        from .tl.types import (
            ChannelParticipantsRecent, ChannelParticipantsSearch,
            InputChannel,
        )

        if not hasattr(self._peer, 'channel_id'):
            self._done = True
            return

        channel = InputChannel(
            channel_id=self._peer.channel_id,
            access_hash=self._peer.access_hash,
        )
        filt = (ChannelParticipantsSearch(q=self._search)
                if self._search else ChannelParticipantsRecent())
        chunk = min(200, int(self._left) if self._left != float('inf') else 200)

        result = await self._client(GetParticipantsRequest(
            channel=channel, filter=filt,
            offset=self._offset, limit=chunk, hash=0,
        ))
        self._client._process_entities(result)
        users = getattr(result, 'users', [])
        self._buffer.extend(users)
        self._offset += len(users)

        if not users or len(users) < chunk:
            self._done = True
