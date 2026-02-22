"""TL core protocol types."""
import gzip
import logging
import struct

from ..tlobject import TLObject, TLRequest

__log__ = logging.getLogger(__name__)


class GzipPacked(TLObject):
    CONSTRUCTOR_ID = 0x3072cfa1

    def __init__(self, data: bytes):
        self.data = data

    @staticmethod
    def gzip_if_smaller(content_related: bool, data: bytes) -> bytes:
        """Return gzip-packed data if it's smaller, otherwise original."""
        if content_related:
            try:
                gzipped = gzip.compress(data)
                if len(gzipped) < len(data):
                    packed = GzipPacked(gzipped)
                    return bytes(packed)
            except Exception:
                pass
        return data

    def _bytes(self) -> bytes:
        compressed = gzip.compress(self.data)
        return (struct.pack('<I', self.CONSTRUCTOR_ID) +
                TLObject.serialize_bytes(compressed))

    @classmethod
    def from_reader(cls, reader):
        data = reader.tgread_bytes()
        return cls(gzip.decompress(data))

    def to_dict(self):
        return {'_': 'GzipPacked', 'data': self.data}


class MessageContainer(TLObject):
    CONSTRUCTOR_ID = 0x73f1f8dc
    MAXIMUM_SIZE = 1044456  # 1MB
    MAXIMUM_LENGTH = 100

    def __init__(self, messages):
        self.messages = messages

    @classmethod
    def from_reader(cls, reader):
        from ...extensions import BinaryReader
        from ...extensions.binaryreader import RawObject

        count = reader.read_int()
        messages = []
        for _ in range(count):
            from .tlmessage import TLMessage
            msg_id  = reader.read_long()
            seq_no  = reader.read_int()
            length  = reader.read_int()

            # CRITICAL: scope each inner message to EXACTLY `length` bytes.
            # Without this, if any inner parser reads the wrong number of bytes,
            # ALL subsequent messages in the container are misaligned.  In the
            # worst case the next msg_id read hits zero-padding bytes and the
            # eventual tgread_object() raises TypeNotFoundError(0x00000000, ...),
            # which is what produces the misleading "SCHEMA MISMATCH 0x00000000".
            inner_bytes = reader.read(length)
            with BinaryReader(inner_bytes) as inner:
                try:
                    obj = inner.tgread_object()
                except Exception as e:
                    __log__.debug(
                        'Failed to parse inner message (msg_id=%d, length=%d): %s',
                        msg_id, length, e
                    )
                    obj = RawObject(0, inner_bytes)

            messages.append(TLMessage(msg_id, seq_no, obj))
        return cls(messages)

    def to_dict(self):
        return {'_': 'MessageContainer', 'messages': self.messages}


class RpcResult(TLObject):
    CONSTRUCTOR_ID = 0xf35c6d01

    def __init__(self, req_msg_id: int, body):
        self.req_msg_id = req_msg_id
        self.body = body
        self.error = None

    @classmethod
    def from_reader(cls, reader):
        from ...errors import TypeNotFoundError
        from ...extensions.binaryreader import RawObject
        from ...extensions import BinaryReader

        req_msg_id = reader.read_long()
        inner_code  = reader.read_int(signed=False)

        if inner_code == 0x2144ca19:  # rpc_error
            error_code    = reader.read_int()
            error_message = reader.tgread_string()
            result = cls(req_msg_id, None)
            result.error  = (error_code, error_message)

        elif inner_code == GzipPacked.CONSTRUCTOR_ID:
            packed_data = reader.tgread_bytes()
            try:
                unpacked = gzip.decompress(packed_data)
            except Exception as e:
                __log__.warning(
                    'RpcResult: gzip decompression failed for req_msg_id=%d: %s',
                    req_msg_id, e
                )
                return cls(req_msg_id, RawObject(GzipPacked.CONSTRUCTOR_ID, packed_data))

            with BinaryReader(unpacked) as inner:
                try:
                    body = inner.tgread_object()
                except TypeNotFoundError as e:
                    __log__.debug(
                        'RpcResult: unknown constructor 0x%08x in gzip body '
                        '(req_msg_id=%d) — schema may be outdated',
                        e.invalid_constructor_id, req_msg_id
                    )
                    body = RawObject(e.invalid_constructor_id, e.remaining)
                except Exception as e:
                    __log__.warning(
                        'RpcResult: parse error in gzip body (req_msg_id=%d): '
                        '%s: %s. Raw gzip bytes (first 64): %s',
                        req_msg_id, type(e).__name__, e,
                        unpacked[:64].hex()
                    )
                    body = RawObject(0xdeadbeef, unpacked)

            result = cls(req_msg_id, body)

        else:
            # Non-gzip body — seek back and re-read including the constructor.
            reader.seek(-4)
            try:
                body = reader.tgread_object()
            except TypeNotFoundError as e:
                __log__.debug(
                    'RpcResult: unknown constructor 0x%08x in raw body '
                    '(req_msg_id=%d) — schema may be outdated',
                    e.invalid_constructor_id, req_msg_id
                )
                body = RawObject(e.invalid_constructor_id, e.remaining)
            except Exception as e:
                __log__.warning(
                    'RpcResult: parse error in raw body (req_msg_id=%d): '
                    '%s: %s',
                    req_msg_id, type(e).__name__, e
                )
                body = RawObject(inner_code, b'')

            result = cls(req_msg_id, body)

        return result

    def to_dict(self):
        return {'_': 'RpcResult', 'req_msg_id': self.req_msg_id, 'body': self.body}
