"""
tglib/requestiter.py  —  Async iterator base for paginated Telegram requests.

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
import asyncio


class RequestIter:
    """
    Base class for async iterators that page through Telegram API results.

    Subclasses must implement:
        async _init(self, **kwargs)   — called once on first iteration
        async _load_next_chunk(self)  — fill self.buffer; return True to stop

    Subclasses may set:
        self.total  — total count (set once known)
        self.left   — items remaining (set to trigger early stop)
    """

    def __init__(self, client, limit, *, wait_time=None, **kwargs):
        self.client    = client
        self.reverse   = kwargs.pop('reverse', False)
        self.wait_time = wait_time
        self.limit     = max(float('inf') if limit is None else limit, 0)
        self.left      = self.limit
        self.buffer    = None
        self.index     = 0
        self.last_load = 0
        self.total     = None
        self._kwargs   = kwargs

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.buffer is None:
            self.buffer = []
            await self._init(**self._kwargs)

        # Yield from current buffer
        if self.index < len(self.buffer):
            result = self.buffer[self.index]
            self.index  += 1
            self.left   -= 1
            return result

        if self.left <= 0:
            raise StopAsyncIteration

        # Load next chunk
        self.buffer.clear()
        self.index = 0

        # Throttle if needed
        if self.wait_time and self.last_load:
            diff = self.wait_time - (asyncio.get_event_loop().time() - self.last_load)
            if diff > 0:
                await asyncio.sleep(diff)

        self.last_load = asyncio.get_event_loop().time()

        done = await self._load_next_chunk()
        if done or not self.buffer:
            raise StopAsyncIteration

        result = self.buffer[self.index]
        self.index  += 1
        self.left   -= 1
        return result

    async def _init(self, **kwargs):
        """Called once before first iteration.  Override in subclass."""

    async def _load_next_chunk(self) -> bool:
        """
        Load the next page into self.buffer.

        Return True to signal that iteration is complete, or False/None
        to continue.  May also set self.left or self.total.
        """
        raise NotImplementedError

    async def collect(self) -> list:
        """Exhaust the iterator and return all results as a list."""
        return [item async for item in self]

    async def close(self):
        """Release any resources held by this iterator."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
