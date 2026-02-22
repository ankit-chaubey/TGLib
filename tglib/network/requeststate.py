"""RequestState - tracks the lifecycle of a single pending request."""
import asyncio


class RequestState:
    """Wraps a TLRequest with its future result and tracking info."""

    def __init__(self, request, *, after=None):
        self.request = request
        self.future = asyncio.get_event_loop().create_future()
        self.msg_id = None
        self.after = after  # Optional[RequestState] for ordered sending

    def __repr__(self):
        return f'RequestState({type(self.request).__name__}, msg_id={self.msg_id})'
