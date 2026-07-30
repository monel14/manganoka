from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = Path(__file__).resolve().parent.parent / "manganoka-indexing-bc242a69a6fc.json"
SCOPES = ["https://www.googleapis.com/auth/indexing"]
BASE_URL = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")


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
    Async-safe : l'appel bloquant est exécuté dans un thread de l'executor.
    """
    if not urls:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _publish_urls_sync, urls)
