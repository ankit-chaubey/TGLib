"""TLMessage - represents a single MTProto message."""


class TLMessage:
    """Represents an MTProto message with msg_id, seq_no and obj."""
    SIZE_OVERHEAD = 12  # msg_id (8) + seq_no (4) + length (4) ... wait 12?

    def __init__(self, msg_id: int, seq_no: int, obj):
        self.msg_id = msg_id
        self.seq_no = seq_no
        self.obj = obj

    def __repr__(self):
        return f'TLMessage(msg_id={self.msg_id}, seq_no={self.seq_no}, obj={self.obj})'
