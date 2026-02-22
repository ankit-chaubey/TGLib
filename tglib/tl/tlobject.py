"""
Base TLObject and TLRequest classes for tglib.
All generated TL types and functions inherit from these.
"""
import base64
import json
import struct
from datetime import datetime, date, timedelta, timezone
import time

_EPOCH_NAIVE = datetime(*time.gmtime(0)[:6])
_EPOCH = _EPOCH_NAIVE.replace(tzinfo=timezone.utc)


def _datetime_to_timestamp(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((dt - _EPOCH).total_seconds())
    return struct.unpack('i', struct.pack('I', secs & 0xffffffff))[0]


def _json_default(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    elif isinstance(value, datetime):
        return value.isoformat()
    return repr(value)


class TLObject:
    """Base class for all TL types."""
    CONSTRUCTOR_ID = None
    SUBCLASS_OF_ID = None

    @staticmethod
    def serialize_bytes(data) -> bytes:
        """Serialize bytes/str using Telegram's byte encoding."""
        if not isinstance(data, bytes):
            if isinstance(data, str):
                data = data.encode('utf-8')
            else:
                raise TypeError(f'bytes or str expected, not {type(data)}')

        r = []
        if len(data) < 254:
            padding = (len(data) + 1) % 4
            padding = 0 if padding == 0 else 4 - padding
            r.append(bytes([len(data)]))
            r.append(data)
        else:
            padding = len(data) % 4
            padding = 0 if padding == 0 else 4 - padding
            r.append(bytes([
                254,
                len(data) % 256,
                (len(data) >> 8) % 256,
                (len(data) >> 16) % 256,
            ]))
            r.append(data)

        r.append(bytes(padding))
        return b''.join(r)

    @staticmethod
    def serialize_datetime(dt) -> bytes:
        if not dt and not isinstance(dt, timedelta):
            return b'\0\0\0\0'
        if isinstance(dt, datetime):
            dt = _datetime_to_timestamp(dt)
        elif isinstance(dt, date):
            dt = _datetime_to_timestamp(datetime(dt.year, dt.month, dt.day))
        elif isinstance(dt, float):
            dt = int(dt)
        elif isinstance(dt, timedelta):
            dt = _datetime_to_timestamp(datetime.utcnow() + dt)
        if isinstance(dt, int):
            return struct.pack('<i', dt)
        raise TypeError(f'Cannot interpret "{dt}" as a date.')

    @staticmethod
    def pretty_format(obj, indent=None):
        if indent is None:
            if isinstance(obj, TLObject):
                obj = obj.to_dict()
            if isinstance(obj, dict):
                return '{}({})'.format(
                    obj.get('_', 'dict'),
                    ', '.join(f'{k}={TLObject.pretty_format(v)}'
                               for k, v in obj.items() if k != '_')
                )
            elif isinstance(obj, (str, bytes)):
                return repr(obj)
            elif hasattr(obj, '__iter__'):
                return '[{}]'.format(', '.join(TLObject.pretty_format(x) for x in obj))
            return repr(obj)
        else:
            result = []
            if isinstance(obj, TLObject):
                obj = obj.to_dict()
            if isinstance(obj, dict):
                result.append(obj.get('_', 'dict'))
                result.append('(')
                if obj:
                    result.append('\n')
                    indent += 1
                    for k, v in obj.items():
                        if k == '_':
                            continue
                        result.append('\t' * indent)
                        result.append(k)
                        result.append('=')
                        result.append(TLObject.pretty_format(v, indent))
                        result.append(',\n')
                    result.pop()
                    indent -= 1
                    result.append('\n')
                    result.append('\t' * indent)
                result.append(')')
            elif isinstance(obj, (str, bytes)):
                result.append(repr(obj))
            elif hasattr(obj, '__iter__'):
                result.append('[\n')
                indent += 1
                for x in obj:
                    result.append('\t' * indent)
                    result.append(TLObject.pretty_format(x, indent))
                    result.append(',\n')
                indent -= 1
                result.append('\t' * indent)
                result.append(']')
            else:
                result.append(repr(obj))
            return ''.join(result)

    def to_dict(self):
        raise NotImplementedError

    def to_json(self, fp=None, default=_json_default, **kwargs):
        d = self.to_dict()
        if fp:
            return json.dump(d, fp, default=default, **kwargs)
        return json.dumps(d, default=default, **kwargs)

    def __eq__(self, o):
        return isinstance(o, type(self)) and self.to_dict() == o.to_dict()

    def __ne__(self, o):
        return not isinstance(o, type(self)) or self.to_dict() != o.to_dict()

    def __str__(self):
        return TLObject.pretty_format(self)

    def stringify(self):
        return TLObject.pretty_format(self, indent=0)

    def __bytes__(self):
        try:
            return self._bytes()
        except AttributeError:
            raise TypeError('a TLObject was expected but found something else')

    def _bytes(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def from_reader(cls, reader):
        raise NotImplementedError


class TLRequest(TLObject):
    """Represents a content-related TLObject (a request that can be sent)."""

    @staticmethod
    def read_result(reader):
        return reader.tgread_object()

    async def resolve(self, client, utils):
        pass
