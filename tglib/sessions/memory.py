"""In-memory session implementation."""
from ..crypto import AuthKey


class MemorySession:
    """Stores session data in memory (lost on exit)."""

    def __init__(self):
        self._dc_id = 2
        self._server_address = '149.154.167.51'
        self._port = 443
        self._auth_key = AuthKey(None)
        self._takeout_id = None

    @property
    def dc_id(self): return self._dc_id

    @dc_id.setter
    def dc_id(self, value):
        self._dc_id = value

    @property
    def server_address(self): return self._server_address

    @property
    def port(self): return self._port

    @property
    def auth_key(self): return self._auth_key

    @auth_key.setter
    def auth_key(self, value):
        self._auth_key = value

    def set_dc(self, dc_id: int, server_address: str, port: int):
        self._dc_id = dc_id
        self._server_address = server_address
        self._port = port

    def save(self):
        pass  # Nothing to persist in memory

    def close(self):
        pass

    def clone(self, to_instance=None):
        cloned = to_instance or MemorySession()
        cloned._dc_id = self._dc_id
        cloned._server_address = self._server_address
        cloned._port = self._port
        cloned._auth_key = self._auth_key
        cloned._takeout_id = self._takeout_id
        return cloned

    def get_update_state(self, entity_id: int):
        return None

    def set_update_state(self, entity_id: int, state):
        pass

    def get_entity_rows_by_phone(self, phone: str):
        return None

    def get_entity_rows_by_username(self, username: str):
        return None

    def get_entity_rows_by_name(self, first_name: str, last_name: str):
        return None

    def get_entity_rows_by_id(self, entity_id: int, exact: bool = True):
        return None

    def process_entities(self, tlo):
        pass

    def cache_file(self, md5_digest, file_size, instance):
        pass

    def get_file(self, md5_digest, file_size, cls):
        return None
