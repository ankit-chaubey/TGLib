"""
EntityCache - fast in-memory peer/access_hash cache.

Ported from Telethon v1 (_updates/entitycache.py and _updates/session.py).
Provides O(1) lookup of InputPeer from numeric entity IDs without hitting
the SQLite session for every message send/forward.

Usage in TelegramClient:
    self._entity_cache = EntityCache()
    ...
    # After any API call that returns users/chats:
    self._entity_cache.extend(result.users, getattr(result, 'chats', []))
    ...
    # Resolving a peer:
    cached = self._entity_cache.get(user_id)
    if cached:
        input_peer = cached._as_input_peer()
"""
from enum import IntEnum


class EntityType(IntEnum):
    """
    Entity type codes (printable ASCII so they can be stored as a single char).

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

    def is_user_type(self):
        return self in (EntityType.USER, EntityType.BOT)

    def is_channel_type(self):
        return self in (EntityType.CHANNEL, EntityType.MEGAGROUP,
                        EntityType.GIGAGROUP)


class CachedEntity:
    """
    Minimal entity record: type + id + access_hash.
    Knows how to convert itself into the correct InputPeer subtype.
    """
    __slots__ = ('ty', 'id', 'hash')

    def __init__(self, ty: EntityType, id: int, hash: int):
        self.ty = ty
        self.id = id
        self.hash = hash

    def _as_input_peer(self):
        from ..tl.types import (
            InputPeerUser, InputPeerChat, InputPeerChannel
        )
        if self.ty in (EntityType.USER, EntityType.BOT):
            return InputPeerUser(self.id, self.hash)
        elif self.ty == EntityType.GROUP:
            return InputPeerChat(self.id)
        else:
            return InputPeerChannel(self.id, self.hash)

    def __repr__(self):
        return (f'CachedEntity(ty={chr(self.ty)!r}, id={self.id}, '
                f'hash={self.hash})')


class EntityCache:
    """
    Thread-safe (asyncio single-threaded) in-memory entity cache.

    Maps entity_id -> CachedEntity.
    self_id / self_bot are stored separately for quick 'get_me' lookups.
    """

    def __init__(self):
        self._map: dict = {}   # id -> (access_hash, EntityType)
        self.self_id: int = None
        self.self_bot: bool = None

    def set_self_user(self, id: int, bot: bool, hash: int):
        self.self_id = id
        self.self_bot = bot
        if hash:
            ty = EntityType.BOT if bot else EntityType.USER
            self._map[id] = (hash, ty)

    def get(self, id: int):
        """Return a CachedEntity or None."""
        entry = self._map.get(id)
        if entry is None:
            return None
        hash_, ty = entry
        return CachedEntity(ty, id, hash_)

    def extend(self, users, chats):
        """
        Bulk-update from any API result that carries .users and .chats.
        Skips 'min' constructors (Telegram may omit access_hash in those).
        """
        for u in users:
            ah = getattr(u, 'access_hash', None)
            if ah and not getattr(u, 'min', False):
                ty = EntityType.BOT if getattr(u, 'bot', False) \
                    else EntityType.USER
                self._map[u.id] = (ah, ty)

        for c in chats:
            ah = getattr(c, 'access_hash', None)
            if ah and not getattr(c, 'min', False):
                if getattr(c, 'megagroup', False):
                    ty = EntityType.MEGAGROUP
                elif getattr(c, 'gigagroup', False):
                    ty = EntityType.GIGAGROUP
                elif hasattr(c, 'broadcast'):
                    ty = EntityType.CHANNEL
                else:
                    ty = EntityType.GROUP
                self._map[c.id] = (ah, ty)
            elif not ah:
                # small Chat - no access_hash needed
                self._map[c.id] = (0, EntityType.GROUP)

    def put(self, id: int, ty: EntityType, hash: int):
        """Manually insert a single entity."""
        self._map[id] = (hash, ty)

    def invalidate(self, id: int):
        self._map.pop(id, None)

    def __len__(self):
        return len(self._map)

    def __contains__(self, id: int):
        return id in self._map
