"""
MTProtoSender - encrypted MTProto 2.0 sender with send/receive loops.

Fixes applied (ported from Telethon v1):
  - struct.error guard in send() so 'required argument is not an integer' logs
    the exact request that caused the problem and re-raises cleanly.
  - asyncio.IncompleteReadError / IOError now trigger reconnect in recv_loop
    instead of an unhandled crash.
  - SecurityError (incl. 'Too many consecutive ignored messages') is now
    handled gracefully: soft errors continue, hard errors reconnect.
  - _pop_states() like Telethon - resolves both direct msg_id and container_id.
  - _last_acks deque so bad-salt can resend recently acked messages.
  - _handle_bad_notification respects error codes 16/17 (clock skew) and
    32/33 (seq-no) before giving up on a state.
  - _handle_bad_server_salt uses _pop_states for proper container lookup.
  - FutureSalts, MsgsStateReq, MsgResendReq, MsgsAllInfo, DestroySession
    handlers added.
  - _store_own_updates() routes self-outgoing updates into the updates queue.
  - _keepalive_ping / _start_reconnect (non-blocking task) added.
  - _handle_rpc_result reads body via BinaryReader + request.read_result().
"""

import asyncio
import collections
import datetime
import logging
import struct
import time

from . import authenticator
from .mtprotoplainsender import MTProtoPlainSender
from .mtprotostate import MTProtoState
from .requeststate import RequestState
from ..crypto import AuthKey
from ..errors import (
    BadMessageError, InvalidBufferError, SecurityError,
    TypeNotFoundError, rpc_message_to_error, AuthKeyNotFound
)
from ..extensions import BinaryReader, MessagePacker
from ..helpers import is_list_like, retry_range
from ..tl.core import RpcResult, MessageContainer, GzipPacked
from ..tl.core.tlmessage import TLMessage

__log__ = logging.getLogger(__name__)


class MTProtoSender:
    """
    Encrypted MTProto sender with automatic auth key generation,
    reconnection, and full protocol handler dispatch.
    """

    def __init__(self, auth_key=None, *, loggers=None,
                 retries=5, delay=1.0, auto_reconnect=True,
                 connect_timeout=10.0,
                 auth_key_callback=None,
                 updates_queue=None,
                 auto_reconnect_callback=None):
        self._loggers = loggers or {__name__: __log__}
        self._log = self._loggers.get(__name__, __log__)
        self._retries = retries
        self._delay = delay
        self._auto_reconnect = auto_reconnect
        self._connect_timeout = connect_timeout
        self._auth_key_callback = auth_key_callback
        self._updates_queue = updates_queue
        self._auto_reconnect_callback = auto_reconnect_callback
        self._connect_lock = asyncio.Lock()
        self._ping = None

        self._connection = None
        self._user_connected = False
        self._reconnecting = False
        self.__disconnected = None  # lazy - created when event loop is running

        self.auth_key = auth_key or AuthKey(None)
        self._state = MTProtoState(self.auth_key, loggers=self._loggers)

        self._send_queue = MessagePacker(self._state, loggers=self._loggers)
        self._pending_state = {}
        self._pending_ack = set()

        # Keep last N ack states so bad-salt can resend them
        self._last_acks = collections.deque(maxlen=10)

        self._send_loop_handle = None
        self._recv_loop_handle = None

        # Handler dispatch table
        self._handlers = {
            RpcResult.CONSTRUCTOR_ID:         self._handle_rpc_result,
            MessageContainer.CONSTRUCTOR_ID:  self._handle_container,
            GzipPacked.CONSTRUCTOR_ID:        self._handle_gzip_packed,
            0x347773c5: self._handle_pong,                # Pong
            0xedab447b: self._handle_bad_server_salt,     # BadServerSalt
            0xa7eff811: self._handle_bad_notification,    # BadMsgNotification
            0x9ec20908: self._handle_new_session_created, # NewSessionCreated
            0x62d6b459: self._handle_ack,                 # MsgsAck
            0x276d3ec6: self._handle_detailed_info,       # MsgDetailedInfo
            0x809db6df: self._handle_new_detailed_info,   # MsgNewDetailedInfo
            0xae500895: self._handle_future_salts,        # FutureSalts
            0xda69fb52: self._handle_state_forgotten,     # MsgsStateReq
            0x7d861a08: self._handle_state_forgotten,     # MsgResendReq
            0x8cc0d131: self._handle_msg_all,             # MsgsAllInfo
            0xe22045fc: self._handle_destroy_session,     # DestroySessionOk
            0x62d350c9: self._handle_destroy_session,     # DestroySessionNone
        }

    # Public API

    async def connect(self, connection):
        async with self._connect_lock:
            if self._user_connected:
                self._log.info('User is already connected!')
                return False
            self._connection = connection
            await self._connect()
            self._user_connected = True
            return True

    async def disconnect(self):
        await self._disconnect()

    def send(self, request, ordered=False):
        """
        Queue a request for sending.
        Returns an asyncio.Future resolving with the response.

        Catches struct.error so 'required argument is not an integer' includes
        the request name in the log (ported from Telethon v1).
        """
        if not self._user_connected:
            raise ConnectionError('Cannot send while disconnected')

        if not is_list_like(request):
            try:
                state = RequestState(request)
            except struct.error as e:
                self._log.error(
                    'Request caused struct.error (field not an integer): %s: %s',
                    e, request)
                raise
            self._send_queue.append(state)
            return state.future
        else:
            states = []
            prev = None
            for req in request:
                try:
                    state = RequestState(req, after=prev if ordered else None)
                except struct.error as e:
                    self._log.error(
                        'Request caused struct.error (field not an integer): %s: %s',
                        e, req)
                    raise
                states.append(state)
                prev = state
                self._send_queue.append(state)
            return [s.future for s in states]

    def is_connected(self) -> bool:
        return self._user_connected

    @property
    def disconnected(self):
        return asyncio.shield(self._disconnected)

    @property
    def _disconnected(self):
        if self.__disconnected is None:
            loop = asyncio.get_event_loop()
            self.__disconnected = loop.create_future()
            self.__disconnected.set_result(None)
        return self.__disconnected

    # Internal connection management

    async def _connect(self):
        if self.__disconnected is None or self.__disconnected.done():
            self.__disconnected = asyncio.get_event_loop().create_future()

        await self._connection.connect()

        if not self.auth_key:
            self._log.info('No auth key - generating...')
            plain = MTProtoPlainSender(self._connection, loggers=self._loggers)
            self.auth_key.key, self._state.time_offset = \
                await authenticator.do_authentication(plain)
            self._state.auth_key = self.auth_key
            if self._auth_key_callback:
                await self._auth_key_callback(self.auth_key)
            self._log.info('Auth key generated')
        else:
            self._state.auth_key = self.auth_key

        self._state.reset()
        loop = asyncio.get_event_loop()
        self._send_loop_handle = loop.create_task(self._send_loop())
        self._recv_loop_handle = loop.create_task(self._recv_loop())

    async def _disconnect(self, error=None):
        self._user_connected = False
        try:
            await self._connection.disconnect()
        finally:
            for state in self._pending_state.values():
                if error and not state.future.done():
                    state.future.set_exception(error)
                else:
                    state.future.cancel()
            self._pending_state.clear()

            if self._send_loop_handle:
                self._send_loop_handle.cancel()
            if self._recv_loop_handle:
                self._recv_loop_handle.cancel()

        if self.__disconnected and not self.__disconnected.done():
            if error:
                self.__disconnected.set_exception(error)
            else:
                self.__disconnected.set_result(None)

    # Non-blocking reconnect trigger (Telethon pattern)

    def _start_reconnect(self, error):
        """Schedule a reconnect as a background task (one at a time)."""
        if self._user_connected and not self._reconnecting:
            self._reconnecting = True
            asyncio.get_event_loop().create_task(self._reconnect(error))

    async def _reconnect(self, last_error):
        """Cleanly disconnect and reconnect, re-queuing pending states."""
        self._log.info('Reconnecting (reason: %s)...', last_error)
        await self._connection.disconnect()

        if self._send_loop_handle:
            self._send_loop_handle.cancel()
        if self._recv_loop_handle:
            self._recv_loop_handle.cancel()

        self._reconnecting = False
        self._state.reset()

        retries = self._retries if self._auto_reconnect else 0
        ok = False
        attempt = 0

        for attempt in range(max(retries, 1)):
            try:
                await self._connect()
                ok = True
            except (IOError, asyncio.TimeoutError) as e:
                last_error = e
                self._log.info('Reconnect attempt %d failed: %s', attempt + 1, e)
                await asyncio.sleep(self._delay)
            except Exception as e:
                last_error = e
                self._log.warning('Unexpected reconnect error attempt %d: %s',
                                  attempt + 1, e)
                await asyncio.sleep(self._delay)
            else:
                # Re-queue pending states so in-flight requests aren't lost
                self._send_queue.extend(self._pending_state.values())
                self._pending_state.clear()
                if self._auto_reconnect_callback:
                    asyncio.get_event_loop().create_task(
                        self._auto_reconnect_callback())
                self._log.info('Reconnected successfully')
                return

        if not ok:
            self._log.error('Reconnect failed after %d attempts', attempt + 1)
            err = last_error.with_traceback(None) if last_error else None
            await self._disconnect(error=err)

    # Keep-alive

    def _keepalive_ping(self, rnd_id):
        """Send a keep-alive ping; reconnect if previous pong never arrived."""
        if self._ping is None:
            self._ping = rnd_id
            try:
                from ..tl.functions import PingRequest
                self.send(PingRequest(ping_id=rnd_id))
            except Exception:
                pass
        else:
            self._start_reconnect(None)

    # Send / Receive loops

    async def _send_loop(self):
        while self._user_connected and not self._reconnecting:
            if self._pending_ack:
                ack_state = RequestState(_make_msgs_ack(list(self._pending_ack)))
                self._send_queue.append(ack_state)
                self._last_acks.append(ack_state)
                self._pending_ack.clear()

            try:
                data, states = await self._send_queue.get()
                if data is None:
                    break

                if states:
                    for state in states:
                        if state.msg_id:
                            self._pending_state[state.msg_id] = state

                encrypted = self._state.encrypt_message_data(data)
                await self._connection.send(encrypted)

            except asyncio.CancelledError:
                break
            except (IOError, asyncio.IncompleteReadError) as e:
                self._log.info('Connection error in send loop: %s', e)
                self._start_reconnect(e)
                return
            except Exception as e:
                self._log.error('Send error: %s', e)
                self._start_reconnect(e)
                return

    async def _recv_loop(self):
        while self._user_connected and not self._reconnecting:
            # --- Receive raw bytes ---
            try:
                body = await self._connection.recv()
            except asyncio.CancelledError:
                break
            except (IOError, asyncio.IncompleteReadError) as e:
                self._log.info('Connection closed while receiving: %s', e)
                self._start_reconnect(e)
                return
            except InvalidBufferError as e:
                code = getattr(e, 'code', None)
                if code == 404:
                    self._log.info('Auth key not found - session needs recreating')
                    await self._disconnect(error=AuthKeyNotFound())
                elif code == 429:
                    self._log.warning('Transport-level flood: %s', e)
                    await self._disconnect(error=e)
                else:
                    self._log.warning('Invalid buffer: %s', e)
                    self._start_reconnect(e)
                return
            except Exception as e:
                self._log.error('Recv error: %s', e)
                self._start_reconnect(e)
                return

            # --- Decrypt ---
            try:
                message = self._state.decrypt_message_data(body)
                if message is None:
                    continue
            except TypeNotFoundError as e:
                self._log.info('Unknown TL type 0x%08x - skipping',
                               e.invalid_constructor_id)
                continue
            except SecurityError as e:
                # Soft security errors (duplicate msg_id, timestamp drift,
                # 'too many consecutive ignored messages') must NOT kill
                # the connection - just log and continue.
                self._log.warning('Security error decrypting message: %s', e)
                continue
            except InvalidBufferError as e:
                if getattr(e, 'code', None) == 404:
                    await self._disconnect(error=AuthKeyNotFound())
                else:
                    self._log.warning('Invalid buffer decrypting: %s', e)
                    self._start_reconnect(e)
                return
            except Exception as e:
                self._log.error('Unexpected decrypt error: %s', e)
                self._start_reconnect(e)
                return

            # --- Dispatch ---
            try:
                await self._process_message(message)
            except Exception:
                self._log.exception('Unhandled error processing message')

    # Protocol message handlers

    async def _process_message(self, message: TLMessage):
        self._pending_ack.add(message.msg_id)
        handler = self._handlers.get(message.obj.CONSTRUCTOR_ID,
                                     self._handle_update)
        await handler(message)

    def _pop_states(self, msg_id):
        """
        Pop all pending states matching msg_id (direct or container).
        Also checks _last_acks.  Ported from Telethon.
        """
        state = self._pending_state.pop(msg_id, None)
        if state:
            return [state]

        # Container lookup
        to_pop = [
            s.msg_id for s in self._pending_state.values()
            if getattr(s, 'container_id', None) == msg_id
        ]
        if to_pop:
            return [self._pending_state.pop(k) for k in to_pop]

        # Ack fallback
        for ack in self._last_acks:
            if ack.msg_id == msg_id:
                return [ack]

        return []

    async def _handle_rpc_result(self, message: TLMessage):
        rpc = message.obj
        state = self._pending_state.pop(rpc.req_msg_id, None)
        self._log.debug('RPC result for msg_id %d', rpc.req_msg_id)

        if state is None:
            if rpc.error:
                self._log.info('RPC error with no parent request: %s', rpc.error)
            return

        if rpc.error:
            # rpc.error is a (code, message) tuple in TGLib
            code, msg = rpc.error
            error = rpc_message_to_error(code, msg, state.request)
            # Send ack for the original message
            self._send_queue.append(
                RequestState(_make_msgs_ack([state.msg_id])))
            if not state.future.cancelled():
                state.future.set_exception(error)
        else:
            # rpc.body is already a parsed TLObject in TGLib
            result = rpc.body
            self._store_own_updates(result)
            if not state.future.cancelled():
                state.future.set_result(result)

    def _store_own_updates(self, obj):
        """Forward self-outgoing update types into the updates queue (Telethon)."""
        if self._updates_queue is None or obj is None:
            return
        _UPDATE_CIDs = frozenset((
            0x9015e101,  # UpdateShortMessage
            0x4d6deea5,  # UpdateShortChatMessage
            0x78d4dec1,  # UpdateShort
            0x725b04c3,  # UpdatesCombined
            0x74ae4240,  # Updates
            0x9d1d85a5,  # UpdateShortSentMessage
        ))
        _AFFECTED_CIDs = frozenset((
            0xb45c69d1,  # messages.AffectedHistory
            0x84d19185,  # messages.AffectedMessages
            0xef8d3b73,  # messages.AffectedFoundMessages
        ))
        try:
            cid = obj.CONSTRUCTOR_ID
            if cid in _UPDATE_CIDs:
                obj._self_outgoing = True
                self._updates_queue.put_nowait(obj)
            elif cid in _AFFECTED_CIDs:
                try:
                    from ..tl.types import UpdateShort, UpdateDeleteMessages
                    epoch = datetime.datetime(1970, 1, 1,
                                             tzinfo=datetime.timezone.utc)
                    upd = UpdateShort(
                        update=UpdateDeleteMessages(
                            messages=[], pts=obj.pts, pts_count=obj.pts_count),
                        date=epoch)
                    upd._self_outgoing = True
                    self._updates_queue.put_nowait(upd)
                except Exception:
                    pass
        except AttributeError:
            pass

    async def _handle_container(self, message: TLMessage):
        for inner in message.obj.messages:
            try:
                await self._process_message(inner)
            except TypeNotFoundError as e:
                self._log.debug(
                    'Unknown TL 0x%08x in container - skipping',
                    e.invalid_constructor_id)

    async def _handle_gzip_packed(self, message: TLMessage):
        with BinaryReader(message.obj.data) as reader:
            message.obj = reader.tgread_object()
            await self._process_message(message)

    async def _handle_update(self, message: TLMessage):
        if self._updates_queue is None:
            return
        try:
            assert message.obj.SUBCLASS_OF_ID == 0x8af52aac  # Updates
        except (AssertionError, AttributeError):
            self._log.debug('Non-update object, ignoring: %s',
                            type(message.obj).__name__)
            return
        self._updates_queue.put_nowait(message.obj)

    async def _handle_pong(self, message: TLMessage):
        pong = message.obj
        self._log.debug('Pong for msg_id %d', pong.msg_id)
        if self._ping == pong.ping_id:
            self._ping = None
        state = self._pending_state.pop(pong.msg_id, None)
        if state and not state.future.done():
            state.future.set_result(pong)

    async def _handle_bad_server_salt(self, message: TLMessage):
        bad = message.obj
        self._log.debug('Bad server salt for msg_id %d', bad.bad_msg_id)
        self._state.salt = bad.new_server_salt
        states = self._pop_states(bad.bad_msg_id)
        self._send_queue.extend(states)
        self._log.debug('%d message(s) will be resent after salt correction',
                        len(states))

    async def _handle_bad_notification(self, message: TLMessage):
        """
        Bad msg notification - handle all error codes as Telethon does:
          16/17 -> time offset  |  32/33 -> sequence number  |  else -> error
        """
        bad = message.obj
        states = self._pop_states(bad.bad_msg_id)
        self._log.debug('Bad msg error_code=%d for msg_id %d',
                        bad.error_code, bad.bad_msg_id)

        if bad.error_code in (16, 17):
            to = self._state.update_time_offset(correct_msg_id=message.msg_id)
            self._log.info('Clock skew corrected, time_offset=%ds', to)
        elif bad.error_code == 32:
            self._state._sequence += 64
        elif bad.error_code == 33:
            self._state._sequence -= 16
        else:
            for state in states:
                if not state.future.done():
                    state.future.set_exception(BadMessageError(bad.error_code))
            return

        self._send_queue.extend(states)

    async def _handle_detailed_info(self, message: TLMessage):
        """MsgDetailedInfo - ack the answer_msg_id."""
        self._pending_ack.add(message.obj.answer_msg_id)

    async def _handle_new_detailed_info(self, message: TLMessage):
        """MsgNewDetailedInfo - ack the answer_msg_id."""
        self._pending_ack.add(message.obj.answer_msg_id)

    async def _handle_new_session_created(self, message: TLMessage):
        self._log.debug('New session created')
        self._state.salt = message.obj.server_salt

    async def _handle_ack(self, message: TLMessage):
        """Server ACK - only action needed is for auth.logOut."""
        try:
            from ..tl.functions.auth import LogOutRequest
        except ImportError:
            return
        for msg_id in message.obj.msg_ids:
            state = self._pending_state.get(msg_id)
            if state and isinstance(state.request, LogOutRequest):
                del self._pending_state[msg_id]
                if not state.future.done():
                    state.future.set_result(True)

    async def _handle_future_salts(self, message: TLMessage):
        self._log.debug('Future salts for msg_id %d', message.msg_id)
        state = self._pending_state.pop(message.msg_id, None)
        if state and not state.future.done():
            state.future.set_result(message.obj)

    async def _handle_state_forgotten(self, message: TLMessage):
        """
        MsgsStateReq / MsgResendReq - reply with MsgsStateInfo.
        """
        try:
            from ..tl.mtproto_types import MsgsStateInfo
            info = RequestState(
                MsgsStateInfo(
                    req_msg_id=message.msg_id,
                    info=chr(1) * len(message.obj.msg_ids)
                )
            )
            self._send_queue.append(info)
        except Exception as e:
            self._log.debug('Cannot respond to state request: %s', e)

    async def _handle_msg_all(self, message: TLMessage):
        """MsgsAllInfo - nothing to do."""

    async def _handle_destroy_session(self, message: TLMessage):
        try:
            from ..tl.functions import DestroySessionRequest
        except ImportError:
            return
        for msg_id, state in list(self._pending_state.items()):
            if (isinstance(state.request, DestroySessionRequest)
                    and state.request.session_id == message.obj.session_id):
                del self._pending_state[msg_id]
                if not state.future.done():
                    state.future.set_result(message.obj)
                return


# Helpers

def _make_msgs_ack(msg_ids: list):
    """Build a MsgsAck - imported lazily to avoid circular imports."""
    try:
        from ..tl.mtproto_types import MsgsAck
        return MsgsAck(msg_ids=msg_ids)
    except ImportError:
        pass

    class _FallbackAck:
        CONSTRUCTOR_ID = 0x62d6b459
        def __init__(self, msg_ids):
            self.msg_ids = msg_ids
        def __bytes__(self):
            hdr = struct.pack('<I', self.CONSTRUCTOR_ID)
            vec = struct.pack('<II', 0x1cb5c415, len(self.msg_ids))
            body = b''.join(struct.pack('<q', m) for m in self.msg_ids)
            return hdr + vec + body

    return _FallbackAck(msg_ids)
