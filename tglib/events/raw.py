"""
tglib/events/raw.py  —  Raw update event (no filtering).

TGLib — Copyright (C) Ankit Chaubey <ankitchaubey.dev@gmail.com>
GitHub  : https://github.com/ankit-chaubey/TGLib

Portions ported from Telethon v1 (https://github.com/LonamiWebs/Telethon)
Copyright (C) LonamiWebs — MIT License.
"""
from .common import EventBuilder, EventCommon, name_inner_event


@name_inner_event
class Raw(EventBuilder):
    """
    Fired on every incoming update with no filtering or wrapping.

    Useful for working with update types that don't have a dedicated
    event class, or for debugging.

    Parameters
    ----------
    types : type or tuple of types, optional
        Only fire for updates that are instances of these types.

    Example
    -------
    ::

        from tglib.tl import types

        @client.on(events.Raw(types.UpdateUserStatus))
        async def on_status(update):
            print('Status change:', update)

        # Catch everything
        @client.on(events.Raw)
        async def catch_all(update):
            print(type(update).__name__, update)
    """

    def __init__(self, types=None):
        super().__init__()
        if types is not None and not isinstance(types, (list, tuple)):
            types = (types,)
        self._types = tuple(types) if types else None

    @classmethod
    def build(cls, update, others=None, self_id=None):
        return update   # pass the raw update directly

    def filter(self, update):
        if self._types and not isinstance(update, self._types):
            return None
        return update

    class Event(EventCommon):
        """Not used for Raw — the raw update itself is dispatched."""
        pass
