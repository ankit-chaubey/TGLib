"""GzipPacked TL core type - re-exported from rpcresult for backward compatibility."""
import gzip
import struct

from ..tlobject import TLObject


class GzipPacked(TLObject):
    CONSTRUCTOR_ID = 0x3072cfa1

    def __init__(self, data: bytes):
        self.data = data  # already compressed bytes

    @staticmethod
    def gzip_if_smaller(content_related: bool, data: bytes) -> bytes:
        """Return gzip-packed data if smaller, else original data."""
        if content_related:
            try:
                compressed = gzip.compress(data, compresslevel=6)
                if len(compressed) < len(data):
                    packed = GzipPacked(compressed)
                    return bytes(packed)
            except Exception:
                pass
        return data

    def _bytes(self) -> bytes:
        return (
            struct.pack('<I', self.CONSTRUCTOR_ID) +
            TLObject.serialize_bytes(self.data)
        )

    @classmethod
    def from_reader(cls, reader):
        packed = reader.tgread_bytes()
        return cls(gzip.decompress(packed))

    def to_dict(self):
        return {'_': 'GzipPacked'}
