from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = Path(__file__).resolve().parent.parent / "manganoka-indexing-bc242a69a6fc.json"
SCOPES = ["https://www.googleapis.com/auth/indexing"]
BASE_URL = os.environ.get("BASE_URL", "https://manganoka.xyz/fr").rstrip("/")


@lru_cache(maxsize=1)
def _get_service():
    """Construit le client Google Indexing API une seule fois (mis en cache)."""
    credentials = service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
    )
    return build("indexing", "v3", credentials=credentials, cache_discovery=False)


def _to_absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return f"{BASE_URL}/{url.lstrip('/')}"


def _filter_and_log_unindexed_urls(urls: list[str]) -> list[str]:
    """
    Filtre les URLs pour ne garder que celles qui n'ont pas encore été notifiées à Google dans les dernières 48 heures.
    Permet d'économiser drastiquement le quota gratuit de 200 requêtes par jour de Google.
    """
    if not urls:
        return []
        
    db_path = Path(__file__).resolve().parent.parent / "cache.db"
    
    # S'assurer que la table d'indexation existe dans cache.db
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS google_indexed_urls "
                "(url TEXT PRIMARY KEY, pinged_at REAL)"
            )
            # Table pour tracker les stats quotidiennes
            conn.execute(
                "CREATE TABLE IF NOT EXISTS google_indexing_stats "
                "(date TEXT PRIMARY KEY, requests_sent INTEGER DEFAULT 0, requests_blocked INTEGER DEFAULT 0)"
            )
            conn.commit()
    except Exception as e:
        logger.warning("Google Indexing: Impossible d'initialiser la table de dé-duplication: %s", e)
        return urls # En cas de bug, on ne bloque pas les pings

    unindexed_urls = []
    now = time.time()
    # On dédouble sur une base de 7 jours (168 heures) pour préserver au maximum le quota.
    # Une fois qu'une URL de chapitre est indexée sur Google, elle l'est de manière permanente, pas besoin de repinger.
    one_week_ago = now - (7 * 24 * 3600)
    blocked_count = 0
    
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            for url in urls:
                full_url = _to_absolute(url)
                # Vérifier si l'URL a déjà été notifiée récemment
                row = conn.execute(
                    "SELECT pinged_at FROM google_indexed_urls WHERE url = ?", (full_url,)
                ).fetchone()
                
                if row is None or row[0] < one_week_ago:
                    unindexed_urls.append(url)
                    # Marquer comme notifiée
                    conn.execute(
                        "INSERT OR REPLACE INTO google_indexed_urls VALUES (?, ?)", (full_url, now)
                    )
                else:
                    blocked_count += 1
                    
            # Mettre à jour les stats quotidiennes
            from datetime import date
            today = date.today().isoformat()
            conn.execute(
                "INSERT INTO google_indexing_stats (date, requests_sent, requests_blocked) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "requests_sent = requests_sent + ?, "
                "requests_blocked = requests_blocked + ?",
                (today, len(unindexed_urls), blocked_count, len(unindexed_urls), blocked_count)
            )
            conn.commit()
            
            if blocked_count > 0:
                logger.info(
                    "Google Indexing: %d/%d URLs bloquées (déjà notifiées dans les 7 derniers jours). "
                    "Quota économisé aujourd'hui.",
                    blocked_count, len(urls)
                )
    except Exception as e:
        logger.warning("Google Indexing: Erreur lors de la lecture/écriture en base pour dé-duplication: %s", e)
        return urls

    return unindexed_urls


def _publish_urls_sync(urls: list[str]) -> None:
    """Envoi synchrone — doit être appelé dans un thread séparé."""
    if not SERVICE_ACCOUNT_FILE.exists():
        logger.warning("Fichier de clé Google introuvable (%s). Skip.", SERVICE_ACCOUNT_FILE.name)
        return

    service = _get_service()
    batch = service.new_batch_http_request()

    def _callback(request_id: str, response: dict, exception: Exception | None) -> None:
        if exception:
            logger.warning("Google Indexing API erreur pour %s : %s", request_id, exception)
        else:
            logger.info("Google Indexing API OK pour %s : %s", request_id, response)

    for url in urls:
        full_url = _to_absolute(url)
        batch.add(
            service.urlNotifications().publish(body={"url": full_url, "type": "URL_UPDATED"}),
            callback=_callback,
            request_id=full_url,
        )

    batch.execute()


async def ping_google_indexing(urls: list[str]) -> None:
    """
    Notifie Google via son API d'indexation officielle.
    Filtre d'abord les doublons via SQLite pour économiser au maximum le quota quotidien de 200 requêtes.
    Async-safe : l'appel bloquant est exécuté dans un thread de l'executor.
    """
    if not urls:
        return
        
    # Filtrer les doublons via SQLite
    filtered_urls = _filter_and_log_unindexed_urls(urls)
    if not filtered_urls:
        logger.info("Google Indexing: Toutes les URLs soumises ont déjà été notifiées dans les 7 derniers jours. Skip pour préserver le quota de 200/jour.")
        return
        
    logger.info("Google Indexing: Envoi de %d URLs uniques à Google Indexing: %s", len(filtered_urls), filtered_urls)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _publish_urls_sync, filtered_urls)
