import sqlite3
import time
import json
import inspect
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cache.db"

HOME_TTL_SECONDS    = 5 * 60      # 5 minutes
MANGA_TTL_SECONDS   = 10 * 60     # 10 minutes
CHAPTER_TTL_SECONDS = 30 * 60     # 30 minutes


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    # Activation du mode WAL (Write-Ahead Logging) pour d'excellentes performances concurrentes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, data TEXT, expires REAL)"
    )
    return conn


class _Cache:
    async def get_or_set(self, key: str, ttl_seconds: int, loader):
        with _conn() as conn:
            row = conn.execute(
                "SELECT data, expires FROM cache WHERE key=?", (key,)
            ).fetchone()

            if row and row[1] > time.time():
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    # Cache corrompu, on le recharge
                    pass

            # Support both sync and async loaders, including lambdas returning coroutines
            if inspect.iscoroutinefunction(loader):
                data = await loader()
            else:
                data = loader()
                if inspect.iscoroutine(data):
                    data = await data
            
            conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (key, json.dumps(data, ensure_ascii=False), time.time() + ttl_seconds),
            )
            return data

    def get_keys_by_prefix(self, prefix: str) -> list[str]:
        """Retourne toutes les clés en cache qui commencent par un préfixe donné."""
        with _conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE key LIKE ?", (f"{prefix}%",)
            ).fetchall()
            return [row[0] for row in rows]


cache = _Cache()
