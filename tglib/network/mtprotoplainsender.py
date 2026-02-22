"""
MTProtoPlainSender - sends/receives unencrypted messages during auth key generation.
https://core.telegram.org/mtproto/description#unencrypted-messages
"""
import struct

from .mtprotostate import MTProtoState
from ..errors import InvalidBufferError
from ..extensions import BinaryReader


class MTProtoPlainSender:
    """
    Sends and receives plain (unencrypted) MTProto messages.
    Used only during the auth key generation handshake.
    """

    def __init__(self, connection, *, loggers=None):
        self._connection = connection
        self._state = MTProtoState(auth_key=None, loggers=loggers or {})

    async def send(self, request):
        """Serialize request, send it, wait for response and deserialize it."""
        body = bytes(request)
        msg_id = self._state._get_new_msg_id()

        # auth_key_id (0) + msg_id + length + body
        packet = struct.pack('<qqi', 0, msg_id, len(body)) + body
        await self._connection.send(packet)

        data = await self._connection.recv()
        if len(data) < 20:
            raise InvalidBufferError(data)

        with BinaryReader(data) as reader:
            auth_key_id = reader.read_long()
            assert auth_key_id == 0, f'Expected auth_key_id=0, got {auth_key_id}'
            _msg_id = reader.read_long()
            _length = reader.read_int()
            return reader.tgread_object()
