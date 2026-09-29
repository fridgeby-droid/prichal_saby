"""Small portable DB layer. PostgreSQL in production, SQLite for local/demo use."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_pool = None

class Record(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def row_factory(cursor):
    names = [col.name for col in cursor.description] if cursor.description else []
    return lambda values: Record(zip(names, values))

def postgres():
    return bool(os.getenv('DATABASE_URL', '').strip())

def pool():
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        url = os.environ['DATABASE_URL'].strip()
        for prefix in ('postgresql+psycopg://', 'postgresql+asyncpg://', 'postgres://'):
            if url.startswith(prefix): url = 'postgresql://' + url[len(prefix):]
        if not url.startswith('postgresql://'):
            raise RuntimeError('DATABASE_URL должен быть адресом PostgreSQL')
        _pool = ConnectionPool(url, min_size=1, max_size=5, timeout=10,
            kwargs={'row_factory': row_factory, 'connect_timeout': 10,
                    'options': '-c statement_timeout=15000'}, open=True)
        _pool.wait(timeout=15)
    return _pool

class Connection:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=()):
        return self.raw.execute(sql.replace('?', '%s'), params)

@contextmanager
def connect(path):
    if postgres():
        with pool().connection() as raw:
            yield Connection(raw)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path, timeout=15)
        raw.row_factory = sqlite3.Row
        try:
            with raw: yield raw
        finally: raw.close()

def init(path):
    serial = 'BIGSERIAL PRIMARY KEY' if postgres() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    statements = [
        'CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_key TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(user_id,request_key))',
        f'CREATE TABLE IF NOT EXISTS messages(id {serial}, order_id TEXT, sender TEXT, text TEXT, created DOUBLE PRECISION)',
        f'CREATE TABLE IF NOT EXISTS outbox(id {serial}, chat TEXT, text TEXT, order_id TEXT, sent INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS replies(chat TEXT, message_id BIGINT, order_id TEXT, PRIMARY KEY(chat,message_id))',
        'CREATE TABLE IF NOT EXISTS tg_updates(id BIGINT PRIMARY KEY)',
        'CREATE TABLE IF NOT EXISTS active_chats(user_id TEXT PRIMARY KEY,order_id TEXT)',
        'CREATE TABLE IF NOT EXISTS pickup_cache(key TEXT PRIMARY KEY, data TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL)',
    ]
    with connect(path) as c:
        if not postgres(): c.execute('PRAGMA journal_mode=WAL')
        for sql in statements: c.execute(sql)

@contextmanager
def single_worker():
    """Session lock prevents two production workers sending the same paid order."""
    if not postgres():
        yield
        return
    with pool().connection() as raw:
        if not raw.execute('SELECT pg_try_advisory_lock(728193014)').fetchone()[0]:
            raise RuntimeError('Самовывоз уже запущен: оставьте один экземпляр приложения')
        raw.commit()
        try: yield
        finally:
            raw.execute('SELECT pg_advisory_unlock(728193014)')
            raw.commit()
