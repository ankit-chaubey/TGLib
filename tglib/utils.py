"""
tglib/utils.py  —  Production-grade utility functions.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib
Contact : ankitchaubey.dev@gmail.com

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import imghdr
import io
import math
import mimetypes
import os
import pathlib
import re
import struct
from typing import Optional, Tuple, List

# ── Surrogate helpers (Telegram uses UTF-16 offsets) ──────────────────────────

def add_surrogate(text: str) -> str:
    """Replace SMP characters with surrogate pairs (for UTF-16 offset math)."""
    return ''.join(
        ''.join(chr(y) for y in struct.unpack('<HH', x.encode('utf-16le')))
        if (0x10000 <= ord(x) <= 0x10FFFF) else x
        for x in text
    )


def del_surrogate(text: str) -> str:
    """Reverse add_surrogate."""
    return text.encode('utf-16', 'surrogatepass').decode('utf-16')


def within_surrogate(text: str, index: int) -> bool:
    """True if the character at *index* is a low surrogate."""
    return 0xDC00 <= ord(text[index]) <= 0xDFFF


def strip_text(text: str, entities: list) -> str:
    """Strip whitespace from text, adjusting entity offsets/lengths."""
    if not text:
        return text

    while text and text[-1].isspace():
        for e in entities:
            if e.offset + e.length == len(text):
                e.length -= 1
        text = text[:-1]

    while text and text[0].isspace():
        for e in entities:
            if e.offset != 0:
                e.offset -= 1
            else:
                e.length -= 1
        text = text[1:]

    return text


# ── Peer ID helpers ────────────────────────────────────────────────────────────

def get_peer_id(peer, add_mark: bool = True) -> int:
    """
    Get the integer ID for *peer* (bot-API style if add_mark=True).

    Positive  → user
    Negative  → group  (- chat_id)
    -100xxxxx → channel / megagroup
    """
    from .tl import types  # lazy import to avoid circulars
    if isinstance(peer, int):
        return peer
    if isinstance(peer, types.PeerUser):
        return peer.user_id
    if isinstance(peer, types.PeerChat):
        return -peer.chat_id if add_mark else peer.chat_id
    if isinstance(peer, types.PeerChannel):
        if add_mark:
            return int('-100' + str(peer.channel_id))
        return peer.channel_id
    # Full entity objects
    eid = getattr(peer, 'id', None)
    if eid is not None:
        cls = type(peer).__name__.lower()
        if 'channel' in cls or 'megagroup' in cls:
            return int('-100' + str(eid)) if add_mark else eid
        if 'chat' in cls:
            return -eid if add_mark else eid
        return eid
    raise TypeError(f'Cannot get peer ID from {peer!r}')


def resolve_id(marked_id: int) -> Tuple[int, type]:
    """
    Given a *marked* peer ID, return (raw_id, peer_type).

    peer_type is one of:
      tl.types.PeerUser, PeerChat, PeerChannel
    """
    from .tl import types
    if marked_id >= 0:
        return marked_id, types.PeerUser
    if str(marked_id).startswith('-100'):
        return int(str(marked_id)[4:]), types.PeerChannel
    return -marked_id, types.PeerChat


def get_peer(peer):
    """Convert any peer-like object to a Peer TL type."""
    from .tl import types
    if isinstance(peer, (types.PeerUser, types.PeerChat, types.PeerChannel)):
        return peer
    if isinstance(peer, types.InputPeerUser):
        return types.PeerUser(peer.user_id)
    if isinstance(peer, types.InputPeerChat):
        return types.PeerChat(peer.chat_id)
    if isinstance(peer, types.InputPeerChannel):
        return types.PeerChannel(peer.channel_id)
    if isinstance(peer, int):
        raw_id, cls = resolve_id(peer)
        return cls(raw_id)
    raise TypeError(f'Cannot convert {peer!r} to Peer')


# ── File / media helpers ───────────────────────────────────────────────────────

def is_image(file) -> bool:
    """Best-effort check whether *file* looks like an image."""
    if isinstance(file, str):
        mime, _ = mimetypes.guess_type(file)
        return (mime or '').startswith('image/')
    if isinstance(file, bytes):
        return imghdr.what(io.BytesIO(file)) is not None
    if hasattr(file, 'read'):
        head = file.read(16)
        if hasattr(file, 'seek'):
            file.seek(0)
        return imghdr.what(io.BytesIO(head)) is not None
    return False


def is_list_like(obj) -> bool:
    """True if obj is iterable but not str/bytes."""
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))


def get_extension(media) -> str:
    """Return a dot-prefixed file extension for a document/web media."""
    from .tl import types

    if isinstance(media, types.Document):
        mime = getattr(media, 'mime_type', '') or ''
        ext = mimetypes.guess_extension(mime) or ''
        # Python maps some types to ugly extensions
        fixes = {'.jpe': '.jpg', '.jpeg': '.jpg', '.mpga': '.mp3'}
        return fixes.get(ext, ext)
    if isinstance(media, (types.WebDocument, types.WebDocumentNoProxy)):
        ext = os.path.splitext(media.url)[-1]
        return ext if ext else '.bin'
    return '.bin'


def _get_extension(stream) -> str:
    """Guess file extension from a stream (for upload_file)."""
    name = getattr(stream, 'name', None)
    if name:
        ext = os.path.splitext(name)[-1]
        if ext:
            return ext
    if hasattr(stream, 'read'):
        head = stream.read(16)
        if hasattr(stream, 'seek'):
            stream.seek(0)
        kind = imghdr.what(io.BytesIO(head))
        if kind:
            return '.' + kind
    return ''


def get_appropriated_part_size(file_size: int) -> int:
    """Return the optimal part size in KB for uploading a file of *file_size* bytes."""
    # Mirroring Telethon's table
    for limit, part in (
        (104857600,  64),   # 100 MB → 64 KB parts
        (786432000, 256),   # 750 MB → 256 KB parts
    ):
        if file_size <= limit:
            return part
    return 512  # maximum allowed by Telegram


def get_message_id(message) -> Optional[int]:
    """Extract message ID from a Message object or integer."""
    if message is None:
        return None
    if isinstance(message, int):
        return message
    return getattr(message, 'id', None)


# ── Attributes helper (for document uploads) ──────────────────────────────────

def get_attributes(file, *, mime_type=None, attributes=None,
                   force_document=False, voice_note=False, video_note=False,
                   supports_streaming=False, thumb=None):
    """
    Guess DocumentAttributes and mime_type from a file path or stream.
    Returns (attributes_list, mime_type_str).
    """
    from .tl import types

    name = None
    if isinstance(file, str):
        name = os.path.basename(file)
    elif hasattr(file, 'name'):
        name = os.path.basename(file.name)

    if not mime_type:
        if name:
            mime_type, _ = mimetypes.guess_type(name)
        mime_type = mime_type or 'application/octet-stream'

    attrs = []
    if attributes:
        attrs += list(attributes)

    img_ext = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
    vid_ext = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.m4v')
    aud_ext = ('.mp3', '.ogg', '.flac', '.aac', '.m4a', '.wav', '.opus')

    ext = os.path.splitext(name or '')[-1].lower()

    # Add filename attribute
    if name and not any(isinstance(a, types.DocumentAttributeFilename)
                        for a in attrs):
        attrs.append(types.DocumentAttributeFilename(file_name=name))

    if voice_note:
        attrs.append(types.DocumentAttributeAudio(
            duration=0, voice=True))
    elif video_note:
        attrs.append(types.DocumentAttributeVideo(
            duration=0, w=0, h=0, round_message=True,
            supports_streaming=supports_streaming))
    elif mime_type.startswith('audio/') or ext in aud_ext:
        attrs.append(types.DocumentAttributeAudio(
            duration=0, title=None, performer=None))
    elif (mime_type.startswith('video/') or ext in vid_ext) and not force_document:
        attrs.append(types.DocumentAttributeVideo(
            duration=0, w=0, h=0,
            supports_streaming=supports_streaming))

    return attrs, mime_type


# ── Input media construction ────────────────────────────────────────────────────

def get_input_media(media, *, is_photo=False, attributes=None,
                    force_document=False, voice_note=False, video_note=False,
                    supports_streaming=False, ttl=None):
    """
    Turn a full media object into an Input-variant for re-sending.
    Returns an InputMedia* TL object or raises TypeError.
    """
    from .tl import types

    if isinstance(media, types.MessageMediaPhoto):
        media = media.photo
    if isinstance(media, types.MessageMediaDocument):
        media = media.document
    if isinstance(media, types.Photo):
        return types.InputMediaPhoto(
            id=types.InputPhoto(
                id=media.id,
                access_hash=media.access_hash,
                file_reference=media.file_reference,
            ),
            ttl_seconds=ttl,
        )
    if isinstance(media, types.Document):
        return types.InputMediaDocument(
            id=types.InputDocument(
                id=media.id,
                access_hash=media.access_hash,
                file_reference=media.file_reference,
            ),
            ttl_seconds=ttl,
            query=None,
        )
    raise TypeError(f'Cannot convert {type(media).__name__} to input media')


# ── File info helper (for download) ─────────────────────────────────────────────

class _FileInfo:
    __slots__ = ('dc_id', 'location', 'size')

    def __init__(self, dc_id, location, size):
        self.dc_id   = dc_id
        self.location = location
        self.size     = size


def _get_file_info(media) -> _FileInfo:
    """Extract dc_id, InputFileLocation, and size from a media object."""
    from .tl import types

    dc_id    = None
    size     = None
    location = None

    if isinstance(media, types.MessageMediaPhoto):
        media = media.photo
    if isinstance(media, types.MessageMediaDocument):
        media = media.document

    if isinstance(media, types.Photo):
        size_obj = next(
            (s for s in reversed(media.sizes)
             if isinstance(s, (types.PhotoSize, types.PhotoSizeProgressive))),
            None
        )
        size = getattr(size_obj, 'size', None) or (
            max(size_obj.sizes) if isinstance(size_obj, types.PhotoSizeProgressive)
            else None
        )
        dc_id = media.dc_id
        location = types.InputPhotoFileLocation(
            id=media.id,
            access_hash=media.access_hash,
            file_reference=media.file_reference,
            thumb_size=getattr(size_obj, 'type', '') or '',
        )

    elif isinstance(media, types.Document):
        dc_id    = media.dc_id
        size     = media.size
        location = types.InputDocumentFileLocation(
            id=media.id,
            access_hash=media.access_hash,
            file_reference=media.file_reference,
            thumb_size='',
        )

    elif isinstance(media, (types.InputPhotoFileLocation,
                             types.InputDocumentFileLocation,
                             types.InputPeerPhotoFileLocation,
                             types.InputFileLocation)):
        location = media

    return _FileInfo(dc_id, location, size)


# ── Stripped photo helper ────────────────────────────────────────────────────────

def stripped_photo_to_jpg(stripped: bytes) -> bytes:
    """
    Expand a stripped (thumbnail) photo into a valid JPEG.
    Algorithm from Telegram's official clients.
    """
    if len(stripped) < 3 or stripped[0] != 1:
        return stripped
    header = bytearray(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01'
                       b'\x00\x01\x00\x00\xff\xdb\x00C\x00\x10\x0b\x0c\x0e\x0c'
                       b'\n\x10\x0e\r\x0e\x12\x11\x10\x13\x18(\x1a\x18\x16\x16'
                       b'\x18\x31#%\x1d(3).2\x1c(>\x4b7?4.<\x43GH>KEC<.E]OWQ6i'
                       b'7Vo2\x4bU$\xc8\x8b\x8b\xb5')
    header[164] = stripped[1]
    header[166] = stripped[2]
    return bytes(header) + stripped[3:]


# ── Misc ─────────────────────────────────────────────────────────────────────────

def sanitize_parse_mode(mode):
    """Normalize parse_mode to 'md', 'html', or None."""
    if mode is None:
        return None
    m = str(mode).lower().strip()
    if m in ('md', 'markdown'):
        return 'md'
    if m == 'html':
        return 'html'
    return None


def chunk_list(lst: list, size: int):
    """Yield successive chunks of *size* from *lst*."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
