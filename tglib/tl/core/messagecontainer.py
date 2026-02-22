"""MessageContainer TL core type."""
import struct
from ..tlobject import TLObject


class MessageContainer(TLObject):
    CONSTRUCTOR_ID = 0x73f1f8dc
    MAXIMUM_SIZE = 1044456
    MAXIMUM_LENGTH = 100

    def __init__(self, messages):
        self.messages = messages

    @classmethod
    def from_reader(cls, reader):
        from .tlmessage import TLMessage
        count = reader.read_int()
        messages = []
        for _ in range(count):
            msg_id = reader.read_long()
            seq_no = reader.read_int()
            _length = reader.read_int()
            obj = reader.tgread_object()
            messages.append(TLMessage(msg_id, seq_no, obj))
        return cls(messages)

    def to_dict(self):
        return {'_': 'MessageContainer', 'messages': self.messages}
