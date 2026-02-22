"""TL core protocol types."""
import gzip
import struct

from ..tlobject import TLObject, TLRequest


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
        return (struct.pack('<I', self.CONSTRUCTOR_ID) +
                TLObject.serialize_bytes(self.data))

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
        count = reader.read_int()
        messages = []
        for _ in range(count):
            from .tlmessage import TLMessage
            msg_id = reader.read_long()
            seq_no = reader.read_int()
            length = reader.read_int()
            obj = reader.tgread_object()
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

        req_msg_id = reader.read_long()
        inner_code = reader.read_int(signed=False)

        if inner_code == 0x2144ca19:  # rpc_error
            error_code = reader.read_int()
            error_message = reader.tgread_string()
            result = cls(req_msg_id, None)
            result.error = (error_code, error_message)
        elif inner_code == GzipPacked.CONSTRUCTOR_ID:
            packed_data = reader.tgread_bytes()
            unpacked = gzip.decompress(packed_data)
            from ...extensions import BinaryReader
            with BinaryReader(unpacked) as inner:
                try:
                    body = inner.tgread_object()
                except TypeNotFoundError as e:
                    body = RawObject(e.invalid_constructor_id, e.remaining)
            result = cls(req_msg_id, body)
        else:
            reader.seek(-4)
            try:
                body = reader.tgread_object()
            except TypeNotFoundError as e:
                body = RawObject(e.invalid_constructor_id, e.remaining)
            result = cls(req_msg_id, body)

        return result

    def to_dict(self):
        return {'_': 'RpcResult', 'req_msg_id': self.req_msg_id, 'body': self.body}
