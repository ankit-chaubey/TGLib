"""
AuthKey - wraps the 2048-bit authorization key and provides derived values.
"""
import struct
from hashlib import sha1


class AuthKey:
    """
    Represents the MTProto authorization key (2048-bit).
    Automatically computes aux_hash and key_id from the key bytes.
    """

    def __init__(self, data=None):
        self._key = None
        self.aux_hash = None
        self.key_id = None
        if data is not None:
            self.key = data

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, value):
        if not value:
            self._key = self.aux_hash = self.key_id = None
            return

        if isinstance(value, AuthKey):
            self._key = value._key
            self.aux_hash = value.aux_hash
            self.key_id = value.key_id
            return

        self._key = value
        digest = sha1(self._key).digest()
        # aux_hash = first 8 bytes of SHA1(key) as uint64 LE
        self.aux_hash = struct.unpack_from('<Q', digest, 0)[0]
        # key_id = last 8 bytes of SHA1(key) as uint64 LE
        self.key_id = struct.unpack_from('<Q', digest, 12)[0]

    def calc_new_nonce_hash(self, new_nonce: int, number: int) -> int:
        """
        Calculates new_nonce_hash for DhGen* verification.
        number is 1 for DhGenOk, 2 for DhGenRetry, 3 for DhGenFail.
        """
        nonce_bytes = new_nonce.to_bytes(32, 'little', signed=True)
        data = nonce_bytes + struct.pack('<BQ', number, self.aux_hash)
        return int.from_bytes(sha1(data).digest()[4:20], 'little', signed=True)

    def __bool__(self):
        return bool(self._key)

    def __eq__(self, other):
        return isinstance(other, AuthKey) and other._key == self._key

    def __repr__(self):
        if self._key:
            return f'AuthKey(key_id={self.key_id:#018x})'
        return 'AuthKey(empty)'
