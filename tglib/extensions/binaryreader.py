"""
BinaryReader - utility to read Telegram's binary protocol data.
All numbers are little-endian as per MTProto spec.
"""
import struct
import time
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(*time.gmtime(0)[:6], tzinfo=timezone.utc)


class BinaryReader:
    """Efficient binary data reader for MTProto protocol."""

    def __init__(self, data: bytes):
        self.stream = data or b''
        self.position = 0
        self._last = None

    # ── Primitive reads ────────────────────────────────────────────────────

    def read_byte(self) -> int:
        val, = struct.unpack_from('<B', self.stream, self.position)
        self.position += 1
        return val

    def read_int(self, signed: bool = True) -> int:
        fmt = '<i' if signed else '<I'
        val, = struct.unpack_from(fmt, self.stream, self.position)
        self.position += 4
        return val

    def read_long(self, signed: bool = True) -> int:
        fmt = '<q' if signed else '<Q'
        val, = struct.unpack_from(fmt, self.stream, self.position)
        self.position += 8
        return val

    def read_float(self) -> float:
        val, = struct.unpack_from('<f', self.stream, self.position)
        self.position += 4
        return val

    def read_double(self) -> float:
        val, = struct.unpack_from('<d', self.stream, self.position)
        self.position += 8
        return val

    def read_large_int(self, bits: int, signed: bool = True) -> int:
        """Read an n-bit integer (int128 / int256)."""
        return int.from_bytes(
            self.read(bits // 8), byteorder='little', signed=signed
        )

    def read(self, length: int = -1) -> bytes:
        if length >= 0:
            result = self.stream[self.position:self.position + length]
            self.position += length
        else:
            result = self.stream[self.position:]
            self.position += len(result)
        if length >= 0 and len(result) != length:
            raise BufferError(
                f'Need {length} bytes, only {len(result)} available; '
                f'last={self._last!r}'
            )
        self._last = result
        return result

    def get_bytes(self) -> bytes:
        return self.stream

    # ── Telegram-encoded reads ─────────────────────────────────────────────

    def tgread_bytes(self) -> bytes:
        """Read a Telegram-encoded byte array."""
        first = self.read_byte()
        if first == 254:
            length = (self.read_byte()
                      | (self.read_byte() << 8)
                      | (self.read_byte() << 16))
            padding = length % 4
        else:
            length = first
            padding = (length + 1) % 4

        data = self.read(length)
        if padding:
            self.read(4 - padding)
        return data

    def tgread_string(self) -> str:
        """Read a Telegram-encoded UTF-8 string."""
        return self.tgread_bytes().decode('utf-8', errors='replace')

    def tgread_bool(self) -> bool:
        val = self.read_int(signed=False)
        if val == 0x997275b5:
            return True
        if val == 0xbc799737:
            return False
        raise RuntimeError(f'Invalid boolean code {val:#010x}')

    def tgread_date(self) -> datetime:
        return _EPOCH + timedelta(seconds=self.read_int())

    def tgread_object(self):
        """Read any TL object by dispatching on its constructor ID."""
        from ..tl.alltlobjects import tlobjects
        from ..tl.core import core_objects

        constructor_id = self.read_int(signed=False)

        # Handle primitives
        if constructor_id == 0x997275b5:
            return True
        if constructor_id == 0xbc799737:
            return False
        if constructor_id == 0x1cb5c415:  # Vector
            count = self.read_int()
            return [self.tgread_object() for _ in range(count)]

        # Look up generated types
        clazz = tlobjects.get(constructor_id)
        if clazz is not None:
            return clazz.from_reader(self)

        # Look up core protocol types
        clazz = core_objects.get(constructor_id)
        if clazz is not None:
            return clazz.from_reader(self)

        # Unknown constructor — Telegram is running a newer layer than our schema.
        # Raise TypeNotFoundError so the caller (mtprotosender) can log at DEBUG
        # level and discard the enclosing message frame cleanly.  The remaining
        # bytes in this reader belong only to the current MTProto message frame,
        # so consuming them here does NOT affect subsequent message frames.
        self.seek(-4)
        from ..errors import TypeNotFoundError
        error = TypeNotFoundError(constructor_id, self.read())
        raise error

    def tgread_vector(self) -> list:
        if self.read_int(signed=False) != 0x1cb5c415:
            raise RuntimeError('Expected vector constructor')
        count = self.read_int()
        return [self.tgread_object() for _ in range(count)]

    # ── Position ───────────────────────────────────────────────────────────

    def tell_position(self) -> int:
        return self.position

    def set_position(self, pos: int):
        self.position = pos

    def seek(self, offset: int):
        self.position += offset

    def close(self):
        self.stream = b''

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
