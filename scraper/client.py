from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx

BASE_URL = "https://demonicscans.org"
TIMEOUT_SECONDS = 15

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
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
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
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
    logger.info("Fetching %s", url)

    client = get_http_client()
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(f"Erreur HTTP pour {url}: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        logger.warning("Unexpected content type for %s: %s", url, content_type)

    return response.text
