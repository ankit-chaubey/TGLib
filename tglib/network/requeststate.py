"""RequestState - tracks the lifecycle of a single pending request."""
import asyncio


class RequestState:
    """Wraps a TLRequest with its future result and tracking info."""

    def __init__(self, request, *, after=None):
        self.request      = request
        self.future       = asyncio.get_event_loop().create_future()
        self.msg_id       = None
        self.container_id = None   # set by MessagePacker when packed in a container
        self.after        = after  # Optional[RequestState] for ordered sending

    def __repr__(self):
        return (f'RequestState({type(self.request).__name__}, '
                f'msg_id={self.msg_id}, container_id={self.container_id})')
