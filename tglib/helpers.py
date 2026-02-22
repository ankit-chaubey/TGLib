"""Various helpers for tglib."""
import os
import struct
import logging
import asyncio
from hashlib import sha1

_log = logging.getLogger(__name__)


def generate_random_long(signed: bool = True) -> int:
    """Generate a random 64-bit integer."""
    return int.from_bytes(os.urandom(8), signed=signed, byteorder='little')


def generate_key_data_from_nonce(server_nonce: int, new_nonce: int):
    """
    Generate AES key and IV from nonces for the DH exchange.
    See https://core.telegram.org/mtproto/auth_key#server-dh-parameters
    """
    server_nonce_bytes = server_nonce.to_bytes(16, 'little', signed=True)
    new_nonce_bytes = new_nonce.to_bytes(32, 'little', signed=True)

    hash1 = sha1(new_nonce_bytes + server_nonce_bytes).digest()
    hash2 = sha1(server_nonce_bytes + new_nonce_bytes).digest()
    hash3 = sha1(new_nonce_bytes + new_nonce_bytes).digest()

    key = hash1 + hash2[:12]
    iv = hash2[12:] + hash3 + new_nonce_bytes[:4]
    return key, iv


async def retry_range(retries: int, *, delay: float = 1.0):
    """Async generator that yields attempt numbers, sleeping between them."""
    for attempt in range(retries):
        yield attempt
        if attempt < retries - 1:
            await asyncio.sleep(delay)


def add_surrogate(text: str) -> str:
    """Add surrogates for SMP characters (Telegram uses UTF-16 offsets)."""
    return ''.join(
        ''.join(chr(y) for y in struct.unpack('<HH', x.encode('utf-16le')))
        if (0x10000 <= ord(x) <= 0x10FFFF) else x
        for x in text
    )


def del_surrogate(text: str) -> str:
    return text.encode('utf-16', 'surrogatepass').decode('utf-16')


def get_peer_id(peer) -> int:
    """Extract the numeric peer ID from various peer types."""
    from .tl import types
    if isinstance(peer, types.PeerUser):
        return peer.user_id
    if isinstance(peer, types.PeerChat):
        return -peer.chat_id
    if isinstance(peer, types.PeerChannel):
        return int(f'-100{peer.channel_id}')
    return 0


def is_list_like(obj) -> bool:
    """Check if an object is list-like (but not str/bytes)."""
    return (
        hasattr(obj, '__iter__')
        and not isinstance(obj, (str, bytes))
    )
