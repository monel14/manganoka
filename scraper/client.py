from __future__ import annotations

import logging
import asyncio
from urllib.parse import urljoin

import httpx

BASE_URL = "https://www.mangabats.com"
TIMEOUT_SECONDS = 15

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class NotFoundError(FetchError):
    """La ressource n'existe pas sur le site source (404)."""
    pass


# Client HTTP asynchrone réutilisable
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Retourne le client HTTP asynchrone singleton."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.mangabats.com/",
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    return _http_client


async def close_http_client() -> None:
    """Ferme le client HTTP (à appeler au shutdown de l'app)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def absolute_url(path_or_url: str) -> str:
    return urljoin(BASE_URL, path_or_url)


async def get_html(path_or_url: str) -> str:
    url = absolute_url(path_or_url)
    client = get_http_client()
    
    retries = 3
    backoff = 4.0  # secondes de pause initiales
    
    for attempt in range(retries):
        logger.info("Fetching %s (Attempt %d/%d)", url, attempt + 1, retries)
        try:
            response = await client.get(url)
            if response.status_code == 429:
                logger.warning("HTTP 429 Too Many Requests reçu pour %s. Retrying in %.1fs...", url, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0  # Augmenter exponentiellement le délai
                continue
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                logger.warning("Unexpected content type for %s: %s", url, content_type)
                
            return response.text
            
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise NotFoundError(f"Ressource introuvable: {url}") from exc
            if exc.response.status_code == 429:
                logger.warning("HTTP 429 détecté pour %s. Retrying in %.1fs...", url, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            raise FetchError(f"Erreur HTTP pour {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            if attempt < retries - 1:
                logger.warning("Erreur de connexion pour %s. Retrying in 2.0s...", url)
                await asyncio.sleep(2.0)
                continue
            raise FetchError(f"Erreur HTTP pour {url}: {exc}") from exc
            
    # Si tous les essais échouent avec un 429
    raise FetchError(f"Erreur HTTP 429 permanente (Rate Limited) pour {url}")
