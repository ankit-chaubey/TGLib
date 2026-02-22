"""
tglib error hierarchy.
"""


class TglibError(Exception):
    """Base exception for all tglib errors."""


class TypeNotFoundError(TglibError):
    """Raised when a TL constructor ID is unknown."""
    def __init__(self, constructor_id: int, remaining: bytes = b''):
        self.constructor_id = constructor_id
        self.invalid_constructor_id = constructor_id  # alias used by mtprotosender
        self.remaining = remaining
        super().__init__(
            f'Unknown TL constructor {constructor_id:#010x}. '
            f'Telegram is using a newer layer; update the TL schema.'
        )


class SecurityError(TglibError):
    """Raised when a security check fails (nonce mismatch, invalid hash, etc.)."""


class InvalidBufferError(TglibError):
    """Raised when a received buffer is invalid or too short."""
    def __init__(self, payload: bytes):
        self.payload = payload
        super().__init__(f'Invalid or too-short buffer ({len(payload)} bytes)')


class AuthKeyNotFound(TglibError):
    """Raised when the server doesn't recognize our auth key."""


class BadMessageError(TglibError):
    """Raised when Telegram sends a bad_msg_notification."""
    CODES = {
        16: 'msg_id too low (invalid time offset?)',
        17: 'msg_id too high (invalid time offset?)',
        18: 'incorrect two lower order msg_id bits',
        19: 'container msg_id is the same as msg_id of a previously received message',
        20: 'message too old, and it cannot be verified whether the server has received a message with this msg_id or not',
        32: 'msg_seqno too low',
        33: 'msg_seqno too high',
        34: 'an even msg_seqno expected (for non-content-related message)',
        35: 'odd msg_seqno expected (for content-related message)',
        48: 'incorrect server salt',
        64: 'invalid container',
    }

    def __init__(self, code: int):
        self.code = code
        description = self.CODES.get(code, f'Unknown error code {code}')
        super().__init__(f'Bad message notification {code}: {description}')


class RPCError(TglibError):
    """RPC error returned by Telegram servers."""
    def __init__(self, code: int, message: str, request=None):
        self.code = code
        self.message = message
        self.request = request
        super().__init__(f'RPCError {code}: {message}')


class FloodWaitError(RPCError):
    """FLOOD_WAIT_X - must wait X seconds."""
    def __init__(self, seconds: int, request=None):
        self.seconds = seconds
        super().__init__(420, f'FLOOD_WAIT_{seconds}', request)


class AuthKeyUnregisteredError(RPCError):
    def __init__(self, request=None):
        super().__init__(401, 'AUTH_KEY_UNREGISTERED', request)


class UserNotParticipantError(RPCError):
    def __init__(self, request=None):
        super().__init__(400, 'USER_NOT_PARTICIPANT', request)


class PhoneNumberInvalidError(RPCError):
    def __init__(self, request=None):
        super().__init__(400, 'PHONE_NUMBER_INVALID', request)


class PasswordHashInvalidError(RPCError):
    """Wrong 2FA password — the SRP proof was rejected by Telegram."""
    def __init__(self, request=None):
        super().__init__(400, 'PASSWORD_HASH_INVALID', request)


class SessionPasswordNeededError(RPCError):
    """2FA password required."""
    def __init__(self, request=None):
        super().__init__(401, 'SESSION_PASSWORD_NEEDED', request)


def rpc_message_to_error(code: int, message: str, request=None) -> RPCError:
    """Convert an RPC error code+message into the most specific exception."""
    import re

    if code == 420:
        m = re.match(r'FLOOD_WAIT_(\d+)', message)
        if m:
            return FloodWaitError(int(m.group(1)), request)

    mapping = {
        'AUTH_KEY_UNREGISTERED': AuthKeyUnregisteredError,
        'SESSION_PASSWORD_NEEDED': SessionPasswordNeededError,
        'PHONE_NUMBER_INVALID': PhoneNumberInvalidError,
        'USER_NOT_PARTICIPANT': UserNotParticipantError,
        'PASSWORD_HASH_INVALID': PasswordHashInvalidError,
    }
    cls = mapping.get(message)
    if cls:
        return cls(request)

    return RPCError(code, message, request)
