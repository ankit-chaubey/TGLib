"""
tglib/entitycache.py  —  Production-grade in-memory entity cache.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib
Contact : ankitchaubey.dev@gmail.com

Portions extended from Telethon v1 (_updates/entitycache.py)
Copyright (C) LonamiWebs — MIT License.

Features:
  - O(1) lookup by integer entity ID
  - TTL expiry (entries stale after `ttl` seconds)
  - Optional LRU eviction when cache exceeds `max_size`
  - Thread-safe under asyncio single-threaded concurrency
  - Supports users, bots, small groups, channels, megagroups, gigagroups
"""
import time
from collections import OrderedDict
from enum import IntEnum
from typing import Optional

_DEFAULT_TTL      = 3600   # 1 hour
_DEFAULT_MAX_SIZE = 10_000  # entries


class EntityType(IntEnum):
    """
    Entity type codes (printable ASCII → can be stored as a single char).

    U  (85) = regular user
    B  (66) = bot user
    G  (71) = small group Chat
    C  (67) = broadcast Channel
    M  (77) = megagroup Channel
    E  (69) = gigagroup / broadcast-group Channel
    """
    USER      = ord('U')
    BOT       = ord('B')
    GROUP     = ord('G')
    CHANNEL   = ord('C')
    MEGAGROUP = ord('M')
    GIGAGROUP = ord('E')

    def is_user_type(self) -> bool:
        return self in (EntityType.USER, EntityType.BOT)

    def is_channel_type(self) -> bool:
        return self in (EntityType.CHANNEL, EntityType.MEGAGROUP,
                        EntityType.GIGAGROUP)


class CachedEntity:
    """
    Minimal entity record: type + id + access_hash + expiry timestamp.
    Knows how to produce the correct InputPeer subtype.
    """
    __slots__ = ('ty', 'id', 'hash', '_expires_at')

    def __init__(self, ty: EntityType, id: int, hash: int,
                 ttl: float = _DEFAULT_TTL):
        self.ty         = ty
        self.id         = id
        self.hash       = hash
        self._expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() > self._expires_at

    def refresh(self, ttl: float = _DEFAULT_TTL):
        self._expires_at = time.monotonic() + ttl

    def _as_input_peer(self):
        from .tl.types import InputPeerUser, InputPeerChat, InputPeerChannel
        if self.ty in (EntityType.USER, EntityType.BOT):
            return InputPeerUser(self.id, self.hash)
        elif self.ty == EntityType.GROUP:
            return InputPeerChat(self.id)
        else:
            return InputPeerChannel(self.id, self.hash)

    def __repr__(self) -> str:
        return (f'CachedEntity(ty={chr(self.ty)!r}, id={self.id}, '
                f'hash={self.hash})')


class EntityCache:
    """
    Thread-safe (asyncio single-threaded) in-memory entity cache with
    optional TTL expiry and LRU eviction.

    Parameters
    ----------
    ttl : float
        Seconds before a cache entry is considered stale (default 3600).
    max_size : int
        Maximum number of entries; oldest accessed are evicted (default 10 000).
    """

    def __init__(self, ttl: float = _DEFAULT_TTL,
                 max_size: int = _DEFAULT_MAX_SIZE):
        self._ttl      = ttl
        self._max_size = max_size
        # OrderedDict used as an LRU: most-recently-used at the end
        self._map: OrderedDict = OrderedDict()
        self.self_id:  Optional[int]  = None
        self.self_bot: Optional[bool] = None

    # ── Self-user ──────────────────────────────────────────────────────────────

    def set_self_user(self, id: int, bot: bool, hash: int):
        self.self_id  = id
        self.self_bot = bot
        if hash:
            ty = EntityType.BOT if bot else EntityType.USER
            self._store(id, ty, hash)

    # ── Core CRUD ──────────────────────────────────────────────────────────────

    def get(self, id: int) -> Optional[CachedEntity]:
        """Return a CachedEntity or None (also returns None if expired)."""
        entry = self._map.get(id)
        if entry is None:
            return None
        if entry.expired:
            del self._map[id]
            return None
        # Move to end (LRU touch)
        self._map.move_to_end(id)
        return entry

    def put(self, id: int, ty: EntityType, hash: int):
        """Manually insert or refresh a single entity."""
        self._store(id, ty, hash)

    def invalidate(self, id: int):
        """Remove an entry from the cache."""
        self._map.pop(id, None)

    def clear(self):
        """Remove all entries."""
        self._map.clear()

    # ── Bulk update ────────────────────────────────────────────────────────────

    def extend(self, users, chats):
        """
        Bulk-insert from any API result that carries .users and .chats.
        Skips 'min' constructors (access_hash may be missing in those).
        """
        for u in users or []:
            ah = getattr(u, 'access_hash', None)
            if not getattr(u, 'min', False):
                if ah:
                    ty = EntityType.BOT if getattr(u, 'bot', False) \
                        else EntityType.USER
                    self._store(u.id, ty, ah)

        for c in chats or []:
            if getattr(c, 'min', False):
                continue
            ah = getattr(c, 'access_hash', None)
            if ah:
                if getattr(c, 'megagroup', False):
                    ty = EntityType.MEGAGROUP
                elif getattr(c, 'gigagroup', False):
                    ty = EntityType.GIGAGROUP
                elif hasattr(c, 'broadcast'):
                    ty = EntityType.CHANNEL
                else:
                    ty = EntityType.GROUP
                self._store(c.id, ty, ah)
            else:
                # Small Chat: no access_hash needed
                self._store(c.id, EntityType.GROUP, 0)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _store(self, id: int, ty: EntityType, hash: int):
        if id in self._map:
            entry = self._map[id]
            entry.hash = hash
            entry.ty   = ty
            entry.refresh(self._ttl)
            self._map.move_to_end(id)
        else:
            if len(self._map) >= self._max_size:
                # Evict the oldest (LRU front)
                self._map.popitem(last=False)
            self._map[id] = CachedEntity(ty, id, hash, self._ttl)

    # ── Info ────────────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, id: int) -> bool:
        entry = self._map.get(id)
        if entry is None:
            return False
        if entry.expired:
            del self._map[id]
            return False
        return True

    def __repr__(self) -> str:
        return (f'EntityCache(size={len(self)}, self_id={self.self_id}, '
                f'ttl={self._ttl}s)')

    def stats(self) -> dict:
        """Return diagnostic statistics."""
        now   = time.monotonic()
        total = len(self._map)
        live  = sum(1 for e in self._map.values() if not e.expired)
        return {
            'total':   total,
            'live':    live,
            'expired': total - live,
            'max_size': self._max_size,
            'ttl':     self._ttl,
        }
