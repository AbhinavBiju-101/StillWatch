"""Thin Postgres layer (works with Supabase). One connection per `with` block;
the block commits on success and rolls back on error."""
from contextlib import contextmanager

_URL = ""


def init(url):
    global _URL
    _URL = url


@contextmanager
def connection():
    if not _URL:
        raise RuntimeError("DATABASE_URL is not set")
    import psycopg                      # imported lazily so tests run without it
    from psycopg.rows import dict_row
    # prepare_threshold=None keeps this compatible with Supabase's transaction pooler.
    with psycopg.connect(_URL, row_factory=dict_row, prepare_threshold=None) as conn:
        yield conn


def all(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def run(conn, sql, params=()):
    return conn.execute(sql, params).rowcount


def run_many(conn, sql, rows):
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def run_script(path):
    with open(path, encoding="utf-8") as f, connection() as conn:
        conn.execute(f.read())
