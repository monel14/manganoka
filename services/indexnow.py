import os
import logging
import sqlite3
import time
import httpx
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")


def _to_absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return f"{BASE_URL}/{url.lstrip('/')}"


def _filter_and_log_indexnow_urls(urls: list[str]) -> list[str]:
    """
    Filtre les URLs pour ne garder que celles qui n'ont pas encore été notifiées à IndexNow dans les 7 derniers jours.
    Permet d'économiser de la bande passante et d'éviter les requêtes HTTP redondantes.
    """
    if not urls:
        return []
        
    db_path = Path(__file__).resolve().parent.parent / "cache.db"
    
    # S'assurer que la table d'indexation existe dans cache.db
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS indexnow_indexed_urls "
                "(url TEXT PRIMARY KEY, pinged_at REAL)"
            )
            conn.commit()
    except Exception as e:
        logger.warning("IndexNow: Impossible d'initialiser la table de dé-duplication: %s", e)
        # Formater les URLs de manière absolue avant de les renvoyer
        return [_to_absolute(u) for u in urls]

    unindexed_urls = []
    now = time.time()
    one_week_ago = now - (7 * 24 * 3600)
    
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            for url in urls:
                full_url = _to_absolute(url)
                row = conn.execute(
                    "SELECT pinged_at FROM indexnow_indexed_urls WHERE url = ?", (full_url,)
                ).fetchone()
                
                if row is None or row[0] < one_week_ago:
                    unindexed_urls.append(full_url)
                    conn.execute(
                        "INSERT OR REPLACE INTO indexnow_indexed_urls VALUES (?, ?)", (full_url, now)
                    )
            conn.commit()
    except Exception as e:
        logger.warning("IndexNow: Erreur lors de la lecture/écriture en base pour dé-duplication: %s", e)
        return [_to_absolute(u) for u in urls]

    return unindexed_urls


async def ping_indexnow(urls: list[str]) -> None:
    """Envoie asynchroniquement une liste d'URLs à l'API IndexNow (notifie instantanément Bing, Yandex, etc.)."""
    if not urls:
        return

    # Filtrer les doublons via SQLite
    filtered_urls = _filter_and_log_indexnow_urls(urls)
    if not filtered_urls:
        logger.info("IndexNow: Toutes les URLs soumises ont déjà été notifiées dans les 7 derniers jours. Skip.")
        return

    key = os.environ.get("INDEXNOW_KEY", "7a8e8b2fcd104ef9ac332a018af03324")
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    host = base_url.replace("https://", "").replace("http://", "")

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base_url}/{key}.txt",
        "urlList": filtered_urls
    }
    
    logger.info("IndexNow: Envoi de %d URLs uniques pour indexation instantanée: %s", len(filtered_urls), filtered_urls)
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post("https://api.indexnow.org/indexnow", json=payload, timeout=10.0)
            if r.status_code == 200:
                logger.info("IndexNow: Indexation instantanée réussie ! (HTTP 200)")
            else:
                logger.warning("IndexNow: Retour inattendu de l'API (HTTP %d): %s", r.status_code, r.text)
        except Exception as exc:
            logger.warning("IndexNow: Échec de connexion à l'API IndexNow: %s", exc)
