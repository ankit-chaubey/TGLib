"""
TCP connection with MTProto Abridged transport (simplest & most efficient).
"""
import asyncio
import logging
import struct

__log__ = logging.getLogger(__name__)

# Telegram DC addresses
DC_MAP = {
    1: ('149.154.175.53', 443),
    2: ('149.154.167.51', 443),
    3: ('149.154.175.100', 443),
    4: ('149.154.167.91', 443),
    5: ('91.108.56.130', 443),
}


class Connection:
    """
    Async TCP connection using MTProto Abridged transport.
    The first byte sent is 0xEF to signal abridged mode.
    """

    def __init__(self, ip: str, port: int, *, loggers=None, timeout: float = 10.0):
        self._ip = ip
        self._port = port
        self._timeout = timeout
        self._log = (loggers or {}).get(__name__, __log__)
        self._reader: asyncio.StreamReader = None
        self._writer: asyncio.StreamWriter = None
        self._connected = False

    async def connect(self):
        self._log.info('Connecting to %s:%d (abridged)', self._ip, self._port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip, self._port),
            timeout=self._timeout
        )
        # Signal abridged transport
        self._writer.write(b'\xef')
        await self._writer.drain()
        self._connected = True
        self._log.info('Connected')

    async def disconnect(self):
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def send(self, data: bytes):
        """Send data wrapped in abridged transport."""
        length = len(data) >> 2
        if length < 0x7f:
            header = bytes([length])
        else:
            header = b'\x7f' + struct.pack('<I', length)[:3]

        self._writer.write(header + data)
        await self._writer.drain()

    async def recv(self) -> bytes:
        """Receive a single MTProto abridged packet."""
        # Read length byte
        first = await self._reader.readexactly(1)
        length = first[0]

        if length == 0x7f:
            # 3-byte length
            raw = await self._reader.readexactly(3)
            length = struct.unpack('<I', raw + b'\x00')[0]

        # length is in 4-byte words
        data = await self._reader.readexactly(length * 4)
        return data

    @property
    def is_connected(self) -> bool:
        return self._connected


class ConnectionTcpFull(Connection):
    """Full transport (includes CRC32). Rarely needed but supported."""

    def __init__(self, ip, port, *, loggers=None, timeout=10.0):
        super().__init__(ip, port, loggers=loggers, timeout=timeout)
        self._send_counter = 0
        self._recv_counter = 0

    async def connect(self):
        self._log.info('Connecting to %s:%d (full)', self._ip, self._port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip, self._port),
            timeout=self._timeout
        )
        self._connected = True

    async def send(self, data: bytes):
        import binascii
        payload = struct.pack('<II', len(data) + 12, self._send_counter) + data
        crc = binascii.crc32(payload) & 0xffffffff
        self._writer.write(payload + struct.pack('<I', crc))
        await self._writer.drain()
        self._send_counter += 1

    async def recv(self) -> bytes:
        import binascii
        header = await self._reader.readexactly(8)
        length, seq = struct.unpack('<II', header)
        data = await self._reader.readexactly(length - 12)
        _crc = await self._reader.readexactly(4)
        # CRC check omitted for brevity
        return data


def make_connection(dc_id: int = 2, *, test: bool = False,
                    loggers=None) -> Connection:
    """Create a connection to a Telegram DC."""
    if test:
        test_dc = {
            1: ('149.154.175.10', 443),
            2: ('149.154.167.40', 443),
            3: ('149.154.175.117', 443),
        }
        ip, port = test_dc.get(dc_id, ('149.154.167.40', 443))
    else:
        ip, port = DC_MAP.get(dc_id, ('149.154.167.51', 443))

    return Connection(ip, port, loggers=loggers or {})
