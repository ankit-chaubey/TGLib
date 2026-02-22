"""
MessagePacker - batches outgoing requests into MessageContainer when possible.
"""
import asyncio
import struct
from ..tl.core.messagecontainer import MessageContainer
from ..tl.core.gzippacked import GzipPacked


class MessagePacker:
    """
    Queues outgoing requests and packs multiple into a single
    MessageContainer when possible for efficiency.
    """

    def __init__(self, state, *, loggers):
        self._state = state
        import logging
        self._log = loggers.get(__name__) or logging.getLogger(__name__)
        self._deque = asyncio.Queue()

    def append(self, state):
        self._deque.put_nowait(state)

    def extend(self, states):
        for s in states:
            self.append(s)

    async def get(self):
        """
        Collects pending requests and packs them.
        Returns (data_bytes, [RequestState, ...]) or (None, []).
        """
        states = []
        data = bytearray()

        # Wait for at least one
        first = await self._deque.get()
        if first is None:
            return None, []

        states.append(first)

        # Drain any additional waiting without blocking
        while True:
            try:
                item = self._deque.get_nowait()
                if item is None:
                    break
                states.append(item)
            except asyncio.QueueEmpty:
                break

            if len(states) >= MessageContainer.MAXIMUM_LENGTH:
                break

        # Build the payload
        # Single message - no container needed
        if len(states) == 1:
            state = states[0]
            msg_id = self._state.write_data_as_message(
                data,
                bytes(state.request),
                isinstance(state.request, type) or state.request.CONSTRUCTOR_ID is not None,
                after_id=state.after.msg_id if state.after else None
            )
            state.msg_id = msg_id
            self._log.debug('Sending single %s', type(state.request).__name__)
            return bytes(data), states

        # Multiple messages - wrap in container
        container_buf = bytearray()
        for state in states:
            before = len(container_buf)
            msg_id = self._state.write_data_as_message(
                container_buf,
                bytes(state.request),
                True,
                after_id=state.after.msg_id if state.after else None
            )
            state.msg_id = msg_id

            if len(container_buf) > MessageContainer.MAXIMUM_SIZE:
                # Too large - remove last item and break
                container_buf = container_buf[:before]
                states = states[:-1]
                # Put the overflowed state back
                self._deque.put_nowait(states[-1])
                break

        # Write container header
        data.extend(struct.pack('<I', MessageContainer.CONSTRUCTOR_ID))
        data.extend(struct.pack('<i', len(states)))
        data.extend(container_buf)

        self._log.debug('Sending container with %d messages', len(states))
        return bytes(data), states

    def put_nowait(self, item):
        self._deque.put_nowait(item)
