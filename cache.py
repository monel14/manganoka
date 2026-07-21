import sqlite3
import time
import pickle
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cache.db"

HOME_TTL_SECONDS    = 5 * 60      # 5 minutes
MANGA_TTL_SECONDS   = 10 * 60     # 10 minutes
CHAPTER_TTL_SECONDS = 30 * 60     # 30 minutes


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, data BLOB, expires REAL)"
    )
    return conn


class _Cache:
    def get_or_set(self, key: str, ttl_seconds: int, loader):
        with _conn() as conn:
            row = conn.execute(
                "SELECT data, expires FROM cache WHERE key=?", (key,)
            ).fetchone()

            if row and row[1] > time.time():
                return pickle.loads(row[0])

            data = loader()
            conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (key, pickle.dumps(data), time.time() + ttl_seconds),
            )
            return data


cache = _Cache()
