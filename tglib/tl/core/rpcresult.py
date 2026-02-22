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
        if content_related:
            try:
                gzipped = gzip.compress(data)
                if len(gzipped) < len(data):
                    return bytes(GzipPacked(gzipped))
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
    MAXIMUM_SIZE = 1044456
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
            msg_id = reader.read_long()
            seq_no = reader.read_int()
            length = reader.read_int()

            # Scope each inner message to exactly `length` bytes.
            # This prevents any misaligned parser from corrupting subsequent messages.
            inner_bytes = reader.read(length)
            with BinaryReader(inner_bytes) as inner:
                try:
                    obj = inner.tgread_object()
                except Exception as e:
                    __log__.debug(
                        'Failed to parse container inner msg_id=%d length=%d: %s '
                        'raw(hex)=%s', msg_id, length, e, inner_bytes[:32].hex()
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
            __log__.debug(
                'RpcResult gzip: packed_len=%d packed_head=%s',
                len(packed_data), packed_data[:16].hex()
            )
            try:
                unpacked = gzip.decompress(packed_data)
            except Exception as e:
                __log__.warning('RpcResult: gzip decompress failed req=%d: %s', req_msg_id, e)
                return cls(req_msg_id, RawObject(GzipPacked.CONSTRUCTOR_ID, packed_data))

            __log__.debug(
                'RpcResult gzip: unpacked_len=%d unpacked_head=%s',
                len(unpacked), unpacked[:32].hex()
            )

            with BinaryReader(unpacked) as inner:
                try:
                    body = inner.tgread_object()
                except TypeNotFoundError as e:
                    __log__.warning(
                        'RpcResult: unknown constructor 0x%08x in gzip body req=%d '
                        'unpacked_head=%s',
                        e.invalid_constructor_id, req_msg_id, unpacked[:32].hex()
                    )
                    body = RawObject(e.invalid_constructor_id, e.remaining)
                except Exception as e:
                    __log__.warning(
                        'RpcResult: parse error in gzip body req=%d: %s: %s '
                        'unpacked_head=%s',
                        req_msg_id, type(e).__name__, e, unpacked[:32].hex()
                    )
                    body = RawObject(0xdeadbeef, unpacked)

            result = cls(req_msg_id, body)

        else:
            reader.seek(-4)
            __log__.debug(
                'RpcResult raw: inner_code=0x%08x req=%d', inner_code, req_msg_id
            )
            try:
                body = reader.tgread_object()
            except TypeNotFoundError as e:
                __log__.warning(
                    'RpcResult: unknown constructor 0x%08x in raw body req=%d',
                    e.invalid_constructor_id, req_msg_id
                )
                body = RawObject(e.invalid_constructor_id, e.remaining)
            except Exception as e:
                __log__.warning(
                    'RpcResult: parse error in raw body req=%d: %s: %s',
                    req_msg_id, type(e).__name__, e
                )
                body = RawObject(inner_code, b'')

            result = cls(req_msg_id, body)

        return result

    def to_dict(self):
        return {'_': 'RpcResult', 'req_msg_id': self.req_msg_id, 'body': self.body}
