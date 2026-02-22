"""
MTProtoSender - encrypted MTProto 2.0 sender with send/receive loops.
Handles reconnection, retries, and all protocol-level responses.
"""
import asyncio
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
                 updates_queue=None):
        self._loggers = loggers or {__name__: __log__}
        self._log = self._loggers.get(__name__, __log__)
        self._retries = retries
        self._delay = delay
        self._auto_reconnect = auto_reconnect
        self._connect_timeout = connect_timeout
        self._auth_key_callback = auth_key_callback
        self._updates_queue = updates_queue
        self._connect_lock = asyncio.Lock()

        self._connection = None
        self._user_connected = False
        self._reconnecting = False
        self._disconnected = None  # lazy — created when event loop is running

        self.auth_key = auth_key or AuthKey(None)
        self._state = MTProtoState(self.auth_key, loggers=self._loggers)

        self._send_queue = MessagePacker(self._state, loggers=self._loggers)
        self._pending_state = {}
        self._pending_ack = set()

        self._send_loop_handle = None
        self._recv_loop_handle = None

        # Handler dispatch table
        self._handlers = {
            RpcResult.CONSTRUCTOR_ID:       self._handle_rpc_result,
            MessageContainer.CONSTRUCTOR_ID: self._handle_container,
            GzipPacked.CONSTRUCTOR_ID:      self._handle_gzip_packed,
            0x347773c5: self._handle_pong,              # Pong
            0xedab447b: self._handle_bad_server_salt,   # BadServerSalt
            0xa7eff811: self._handle_bad_notification,  # BadMsgNotification
            0x9ec20908: self._handle_new_session_created, # NewSessionCreated
            0x62d6b459: self._handle_ack,               # MsgsAck
            0x276d3ec6: self._handle_msg_detailed_info, # MsgDetailedInfo
            0x809db6df: self._handle_msg_detailed_info, # MsgNewDetailedInfo
        }

    # ── Public API ─────────────────────────────────────────────────────────

    async def connect(self, connection):
        async with self._connect_lock:
            if self._user_connected:
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
        Returns an asyncio.Future that resolves with the response.
        """
        if not self._user_connected:
            raise ConnectionError('Cannot send while disconnected')

        if not is_list_like(request):
            state = RequestState(request)
            self._send_queue.append(state)
            return state.future
        else:
            states = []
            prev = None
            for req in request:
                state = RequestState(req, after=prev if ordered else None)
                states.append(state)
                prev = state
                self._send_queue.append(state)
            return [s.future for s in states]

    def is_connected(self) -> bool:
        return self._user_connected

    # ── Internal connection management ────────────────────────────────────

    async def _connect(self):
        if self._disconnected is None or self._disconnected.done():
            self._disconnected = asyncio.get_event_loop().create_future()
        await self._connection.connect()

        # Generate auth key if needed
        if not self.auth_key:
            self._log.info('No auth key — generating...')
            plain = MTProtoPlainSender(self._connection, loggers=self._loggers)
            self.auth_key, self._state.time_offset = \
                await authenticator.do_authentication(plain)
            self._state.auth_key = self.auth_key
            if self._auth_key_callback:
                await self._auth_key_callback(self.auth_key)
            self._log.info('Auth key generated (key_id=%#018x)',
                           self.auth_key.key_id)
        else:
            self._state.auth_key = self.auth_key

        self._state.reset()
        self._send_loop_handle = asyncio.ensure_future(self._send_loop())
        self._recv_loop_handle = asyncio.ensure_future(self._recv_loop())

    async def _disconnect(self):
        self._user_connected = False
        self._send_queue.put_nowait(None)
        if self._send_loop_handle:
            self._send_loop_handle.cancel()
        if self._recv_loop_handle:
            self._recv_loop_handle.cancel()
        await self._connection.disconnect()
        # Cancel pending futures
        for state in self._pending_state.values():
            if not state.future.done():
                state.future.cancel()
        self._pending_state.clear()

    # ── Send/Receive loops ────────────────────────────────────────────────

    async def _send_loop(self):
        """Continuously pack and send pending requests."""
        while self._user_connected:
            try:
                data, states = await self._send_queue.get()
                if data is None:
                    break

                # Add any pending acks
                if self._pending_ack:
                    self._send_ack(self._pending_ack)
                    self._pending_ack.clear()

                if states:
                    for state in states:
                        if state.msg_id:
                            self._pending_state[state.msg_id] = state

                encrypted = self._state.encrypt_message_data(data)
                await self._connection.send(encrypted)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error('Send error: %s', e)
                if self._auto_reconnect:
                    await self._try_reconnect()
                return

    async def _recv_loop(self):
        """Continuously receive and dispatch incoming messages."""
        while self._user_connected:
            try:
                body = await self._connection.recv()
                message = self._state.decrypt_message_data(body)
                if message:
                    await self._process_message(message)
            except asyncio.CancelledError:
                break
            except InvalidBufferError as e:
                self._log.warning('Invalid buffer: %s', e)
            except SecurityError as e:
                self._log.error('Security error: %s', e)
                break
            except TypeNotFoundError as e:
                # Unknown top-level update (not an RPC result) — safely skip it.
                # The full frame was already received so stream sync is preserved.
                self._log.debug(
                    'Unknown top-level TL constructor 0x%08x — skipping update',
                    e.invalid_constructor_id
                )
            except Exception as e:
                self._log.error('Recv error: %s', e)
                if self._auto_reconnect:
                    await self._try_reconnect()
                return

    async def _try_reconnect(self):
        if self._reconnecting:
            return
        self._reconnecting = True
        self._log.info('Reconnecting...')
        try:
            async for attempt in retry_range(self._retries, delay=self._delay):
                try:
                    await self._connection.disconnect()
                    await self._connection.connect()
                    self._reconnecting = False
                    self._log.info('Reconnected')

                    # FIX BUG 8: after reconnect the old msg_ids are invalid (new session).
                    # Cancel all pending futures so callers get an error immediately,
                    # then re-queue the underlying requests so they can be retried.
                    pending = list(self._pending_state.values())
                    self._pending_state.clear()
                    for state in pending:
                        if not state.future.done():
                            state.future.cancel()
                        # Re-queue request with a fresh state so the send loop retries it
                        new_state = RequestState(state.request)
                        self._send_queue.append(new_state)

                    # Restart both loops so the connection is fully usable again
                    self._send_loop_handle = asyncio.ensure_future(self._send_loop())
                    self._recv_loop_handle = asyncio.ensure_future(self._recv_loop())
                    return
                except Exception as e:
                    self._log.warning('Reconnect attempt %d failed: %s', attempt + 1, e)
        except Exception:
            pass
        self._reconnecting = False
        self._user_connected = False

    # ── Protocol message handlers ─────────────────────────────────────────

    async def _process_message(self, message: TLMessage):
        handler = self._handlers.get(message.obj.CONSTRUCTOR_ID)
        if handler:
            await handler(message)
        elif self._updates_queue:
            await self._updates_queue.put(message.obj)

    async def _handle_rpc_result(self, message: TLMessage):
        rpc = message.obj
        state = self._pending_state.pop(rpc.req_msg_id, None)
        self._pending_ack.add(message.msg_id)

        if state is None:
            self._log.warning('RPC result for unknown msg_id %d', rpc.req_msg_id)
            return

        if rpc.error:
            code, msg = rpc.error
            error = rpc_message_to_error(code, msg, state.request)
            if not state.future.done():
                state.future.set_exception(error)
        else:
            if not state.future.done():
                state.future.set_result(rpc.body)

    async def _handle_container(self, message: TLMessage):
        from ..errors import TypeNotFoundError
        for inner in message.obj.messages:
            try:
                await self._process_message(inner)
            except TypeNotFoundError as e:
                self._log.debug(
                    'Skipping container sub-message with unknown TL '
                    'constructor 0x%08x (schema needs update)',
                    e.invalid_constructor_id
                )

    async def _handle_gzip_packed(self, message: TLMessage):
        from ..extensions import BinaryReader
        data = message.obj.data
        self._log.debug(
            'GzipPacked top-level: data_len=%d head=%s',
            len(data), data[:32].hex()
        )
        reader = BinaryReader(data)
        inner_obj = reader.tgread_object()
        await self._process_message(TLMessage(
            message.msg_id, message.seq_no, inner_obj
        ))

    async def _handle_pong(self, message: TLMessage):
        pong = message.obj
        state = self._pending_state.pop(pong.msg_id, None)
        if state and not state.future.done():
            state.future.set_result(pong)

    async def _handle_bad_server_salt(self, message: TLMessage):
        bad = message.obj
        self._state.salt = bad.new_server_salt
        state = self._pending_state.get(bad.bad_msg_id)
        if state:
            self._state.update_message_id(state)
            self._send_queue.append(state)

    async def _handle_bad_notification(self, message: TLMessage):
        bad = message.obj
        state = self._pending_state.pop(bad.bad_msg_id, None)
        if state and not state.future.done():
            state.future.set_exception(BadMessageError(bad.error_code))

    async def _handle_new_session_created(self, message: TLMessage):
        self._state.salt = message.obj.server_salt

    async def _handle_ack(self, message: TLMessage):
        pass  # Acknowledgements don't require action

    async def _handle_msg_detailed_info(self, message: TLMessage):
        # FIX BUG 7: server is telling us about a missed message — request a resend.
        # Previously this was incorrectly adding to _pending_ack, which tells the server
        # we already have the message (exactly the opposite of what we want).
        from ..tl.mtproto_types import MsgResendReq
        resend = MsgResendReq(msg_ids=[message.obj.answer_msg_id])
        state = RequestState(resend)
        self._send_queue.append(state)

    def _send_ack(self, msg_ids):
        from ..tl.mtproto_types import MsgsAck
        ack = MsgsAck(msg_ids=list(msg_ids))
        state = RequestState(ack)
        self._send_queue.append(state)
