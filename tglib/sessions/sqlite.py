"""
SQLiteSession - persistent session storage using SQLite.
Stores auth key, DC info, and entity cache.
"""
import os
import datetime
import sqlite3

from .memory import MemorySession
from ..crypto import AuthKey

EXTENSION = '.session'
CURRENT_VERSION = 1


class SQLiteSession(MemorySession):
    """
    Persists the session to a local SQLite file.
    Never share your .session file — it grants full access to your account.
    """

    def __init__(self, session_id: str = None):
        super().__init__()
        self.filename = ':memory:'

        if session_id:
            self.filename = session_id
            if not self.filename.endswith(EXTENSION):
                self.filename += EXTENSION

        self._conn = None
        self._setup()

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.filename, check_same_thread=False)
        return self._conn.cursor()

    def _setup(self):
        c = self._cursor()
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='version'"
        )
        if c.fetchone():
            c.execute('SELECT version FROM version')
            version = c.fetchone()[0]
            if version < CURRENT_VERSION:
                self._upgrade(version)
                c.execute('DELETE FROM version')
                c.execute('INSERT INTO version VALUES (?)', (CURRENT_VERSION,))
                self.save()

            c.execute('SELECT * FROM sessions')
            row = c.fetchone()
            if row:
                self._dc_id, self._server_address, self._port, key, \
                    self._takeout_id = row
                self._auth_key = AuthKey(key)
        else:
            # Fresh database
            c.executescript('''
                CREATE TABLE version (version INTEGER PRIMARY KEY);
                CREATE TABLE sessions (
                    dc_id INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port INTEGER,
                    auth_key BLOB,
                    takeout_id INTEGER
                );
                CREATE TABLE entities (
                    id INTEGER PRIMARY KEY,
                    hash INTEGER NOT NULL,
                    username TEXT,
                    phone TEXT,
                    name TEXT,
                    date INTEGER
                );
            ''')
            c.execute('INSERT INTO version VALUES (?)', (CURRENT_VERSION,))
            self._update_session_table()
            self.save()

        c.close()

    def _upgrade(self, old_version: int):
        pass  # Future upgrades go here

    def _update_session_table(self):
        c = self._cursor()
        c.execute('DELETE FROM sessions')
        c.execute(
            'INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)',
            (self._dc_id, self._server_address, self._port,
             self._auth_key.key if self._auth_key else None,
             self._takeout_id)
        )
        c.close()

    def set_dc(self, dc_id: int, server_address: str, port: int):
        super().set_dc(dc_id, server_address, port)
        self._update_session_table()
        self.save()

    @property
    def dc_id(self):
        return self._dc_id

    @dc_id.setter
    def dc_id(self, value):
        self._dc_id = value
        self._update_session_table()
        self.save()

    @MemorySession.auth_key.setter
    def auth_key(self, value):
        self._auth_key = value
        self._update_session_table()
        self.save()

    def save(self):
        if self._conn:
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def process_entities(self, tlo):
        """Cache entities (users, chats, channels) from any TL object.
        
        Works recursively so it catches entities nested inside Updates,
        UpdatesCombined, and any other wrapper that carries .users/.chats.
        """
        rows = []
        self._collect_entities(tlo, rows, depth=0)
        if rows:
            c = self._cursor()
            c.executemany(
                'INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?)', rows
            )
            c.close()
            self.save()

    def _collect_entities(self, tlo, rows: list, depth: int = 0):
        """Recursively collect (id, hash, username, phone, name, ts) rows."""
        if depth > 4 or tlo is None:
            return
        ts = int(datetime.datetime.utcnow().timestamp())

        # Collect from .users and .chats lists on this object
        entities = []
        if hasattr(tlo, 'users') and tlo.users:
            entities.extend(tlo.users)
        if hasattr(tlo, 'chats') and tlo.chats:
            entities.extend(tlo.chats)
        if hasattr(tlo, 'user') and tlo.user:
            entities.append(tlo.user)

        for e in entities:
            if not hasattr(e, 'id'):
                continue
            eid    = e.id
            ehash  = getattr(e, 'access_hash', 0) or 0
            uname  = getattr(e, 'username', None)
            phone  = getattr(e, 'phone', None)
            fn     = getattr(e, 'first_name', None)
            ln     = getattr(e, 'last_name', None)
            title  = getattr(e, 'title', None)
            name   = title or ' '.join(filter(None, [fn, ln])) or None
            rows.append((eid, ehash, uname, phone, name, ts))

        # Recurse into nested update lists (.updates field)
        nested = getattr(tlo, 'updates', None)
        if nested:
            for item in nested:
                self._collect_entities(item, rows, depth + 1)

    def get_entity_rows_by_id(self, entity_id: int, exact: bool = True):
        c = self._cursor()
        c.execute('SELECT id, hash FROM entities WHERE id=?', (entity_id,))
        return c.fetchone()

    def get_entity_rows_by_username(self, username: str):
        c = self._cursor()
        c.execute(
            'SELECT id, hash FROM entities WHERE username=? '
            'ORDER BY date DESC LIMIT 1',
            (username.lstrip('@'),)
        )
        return c.fetchone()

    def get_entity_rows_by_phone(self, phone: str):
        c = self._cursor()
        c.execute('SELECT id, hash FROM entities WHERE phone=?', (phone,))
        return c.fetchone()

    def get_entity_rows_by_name(self, first_name, last_name):
        name = ' '.join(filter(None, [first_name, last_name]))
        c = self._cursor()
        c.execute('SELECT id, hash FROM entities WHERE name=? LIMIT 1', (name,))
        return c.fetchone()
