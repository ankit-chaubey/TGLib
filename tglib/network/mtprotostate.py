"""
MTProtoState - manages session encryption, message IDs, and sequence numbers.
Implements MTProto 2.0 as per https://core.telegram.org/mtproto/description
"""
import os
import struct
import time
from collections import deque
from hashlib import sha256

from ..crypto import AES
from ..errors import SecurityError, InvalidBufferError
from ..extensions import BinaryReader
from ..tl.core import TLMessage
from ..tl.core.gzippacked import GzipPacked

MAX_RECENT_MSG_IDS = 500
MSG_TOO_NEW_DELTA = 30
MSG_TOO_OLD_DELTA = 300
MAX_CONSECUTIVE_IGNORED = 10


class MTProtoState:
    """
    Holds all state needed to encrypt/decrypt MTProto 2.0 messages:
    - Authorization key
    - Session ID
    - Message ID counter
    - Sequence number
    - Server salt
    - Recent msg_id deque (for duplicate detection)
    """

    def __init__(self, auth_key, loggers=None):
        self.auth_key = auth_key
        self._log = (loggers or {}).get(__name__)
        self.time_offset = 0
        self.salt = 0

        self.id = self._sequence = self._last_msg_id = None
        self._recent_remote_ids = deque(maxlen=MAX_RECENT_MSG_IDS)
        self._highest_remote_id = 0
        self._ignore_count = 0
        self.reset()

    def reset(self):
        self.id = struct.unpack('q', os.urandom(8))[0]
        self._sequence = 0
        self._last_msg_id = 0
        self._recent_remote_ids.clear()
        self._highest_remote_id = 0
        self._ignore_count = 0

    # ── Key derivation (MTProto 2.0) ──────────────────────────────────────

    @staticmethod
    def _calc_key(auth_key: bytes, msg_key: bytes, client: bool):
        """
        Derive AES key+IV from auth_key and msg_key.
        x=0 for client→server, x=8 for server→client.
        """
        x = 0 if client else 8
        sha256a = sha256(msg_key + auth_key[x:x + 36]).digest()
        sha256b = sha256(auth_key[x + 40:x + 76] + msg_key).digest()

        aes_key = sha256a[:8] + sha256b[8:24] + sha256a[24:32]
        aes_iv = sha256b[:8] + sha256a[8:24] + sha256b[24:32]
        return aes_key, aes_iv

    # ── Message ID ────────────────────────────────────────────────────────

    def _get_new_msg_id(self) -> int:
        now = time.time() + self.time_offset
        ns = int((now - int(now)) * 1e9)
        new_msg_id = (int(now) << 32) | (ns << 2)
        if self._last_msg_id >= new_msg_id:
            new_msg_id = self._last_msg_id + 4
        self._last_msg_id = new_msg_id
        return new_msg_id

    def update_message_id(self, message):
        message.msg_id = self._get_new_msg_id()

    def update_time_offset(self, correct_msg_id: int) -> int:
        bad = self._get_new_msg_id()
        old = self.time_offset
        self.time_offset = (correct_msg_id >> 32) - int(time.time())
        if self.time_offset != old:
            self._last_msg_id = 0
        return self.time_offset

    # ── Seq no ───────────────────────────────────────────────────────────

    def _get_seq_no(self, content_related: bool) -> int:
        if content_related:
            result = self._sequence * 2 + 1
            self._sequence += 1
            return result
        return self._sequence * 2

    # ── Framing ──────────────────────────────────────────────────────────

    def write_data_as_message(self, buffer: bytearray, data: bytes,
                               content_related: bool, *, after_id=None) -> int:
        msg_id = self._get_new_msg_id()
        seq_no = self._get_seq_no(content_related)

        if after_id is None:
            body = GzipPacked.gzip_if_smaller(content_related, data)
        else:
            from ..tl.tlobject import TLRequest
            # InvokeAfterMsg wrapper
            wrapped = (
                struct.pack('<Iqq', 0xcb9f372d, after_id, 0) +
                data  # simplified; full impl uses InvokeAfterMsgRequest
            )
            body = GzipPacked.gzip_if_smaller(content_related, data)

        buffer.extend(struct.pack('<qii', msg_id, seq_no, len(body)))
        buffer.extend(body)
        return msg_id

    # ── Encryption / Decryption ───────────────────────────────────────────

    def encrypt_message_data(self, data: bytes) -> bytes:
        """Encrypt data using current auth key (MTProto 2.0)."""
        data = struct.pack('<qq', self.salt, self.id) + data
        padding = os.urandom(-(len(data) + 12) % 16 + 12)

        # msg_key = middle 16 bytes of SHA256(auth_key[88:120] + plaintext + padding)
        msg_key_large = sha256(
            self.auth_key.key[88:88 + 32] + data + padding
        ).digest()
        msg_key = msg_key_large[8:24]

        aes_key, aes_iv = self._calc_key(self.auth_key.key, msg_key, True)
        key_id = struct.pack('<Q', self.auth_key.key_id)
        return key_id + msg_key + AES.encrypt_ige(data + padding, aes_key, aes_iv)

    def decrypt_message_data(self, body: bytes):
        """Decrypt incoming server message data. Returns TLMessage or None."""
        now = time.time()

        if len(body) < 8:
            raise InvalidBufferError(body)

        key_id = struct.unpack('<Q', body[:8])[0]
        if key_id != self.auth_key.key_id:
            raise SecurityError('Server replied with an invalid auth key')

        msg_key = body[8:24]
        aes_key, aes_iv = self._calc_key(self.auth_key.key, msg_key, False)
        body = AES.decrypt_ige(body[24:], aes_key, aes_iv)

        # Verify SHA256
        our_key = sha256(self.auth_key.key[96:96 + 32] + body)
        if msg_key != our_key.digest()[8:24]:
            raise SecurityError("Received msg_key doesn't match expected")

        reader = BinaryReader(body)
        reader.read_long()  # salt
        if reader.read_long() != self.id:
            raise SecurityError('Server replied with wrong session ID')

        remote_msg_id = reader.read_long()
        if remote_msg_id % 2 != 1:
            raise SecurityError('Server sent an even msg_id')

        if (remote_msg_id <= self._highest_remote_id
                and remote_msg_id in self._recent_remote_ids):
            self._count_ignored()
            return None

        remote_sequence = reader.read_int()
        reader.read_int()  # msg_len (padding ignored)

        obj = reader.tgread_object()

        # Time check (skip for BadServerSalt / BadMsgNotification)
        from ..tl.core.rpcresult import RpcResult
        BAD_IDS = {0xedab447b, 0xa7eff811}  # BadServerSalt, BadMsgNotification
        constructor_id = getattr(obj, 'CONSTRUCTOR_ID', None)
        if constructor_id not in BAD_IDS:
            remote_time = remote_msg_id >> 32
            delta = (now + self.time_offset) - remote_time
            if delta > MSG_TOO_OLD_DELTA or -delta > MSG_TOO_NEW_DELTA:
                self._count_ignored()
                return None

        self._recent_remote_ids.append(remote_msg_id)
        self._highest_remote_id = max(self._highest_remote_id, remote_msg_id)
        self._ignore_count = 0

        # Keep time_offset in sync with the server's clock
        self.time_offset = (remote_msg_id >> 32) - int(time.time())

        return TLMessage(remote_msg_id, remote_sequence, obj)

    def _count_ignored(self):
        self._ignore_count += 1
        if self._ignore_count >= MAX_CONSECUTIVE_IGNORED:
            raise SecurityError('Too many consecutive ignored messages')
