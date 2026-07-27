import os
import logging
import httpx

logger = logging.getLogger(__name__)


async def ping_indexnow(urls: list[str]) -> None:
    """Envoie asynchroniquement une liste d'URLs à l'API IndexNow (notifie instantanément Bing, Yandex, etc.)."""
    key = os.environ.get("INDEXNOW_KEY", "7a8e8b2fcd104ef9ac332a018af03324")
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    host = base_url.replace("https://", "").replace("http://", "")
    
    # Formater correctement toutes les URLs pour qu'elles soient absolues
    formatted_urls = []
    for url in urls:
        if url.startswith("/"):
            formatted_urls.append(f"{base_url}{url}")
        elif not url.startswith("http"):
            formatted_urls.append(f"{base_url}/{url}")
        else:
            formatted_urls.append(url)

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base_url}/{key}.txt",
        "urlList": formatted_urls
    }
    
    logger.info("IndexNow: Envoi de %d URLs pour indexation instantanée: %s", len(formatted_urls), formatted_urls)
    
    async with httpx.AsyncClient() as client:
        try:
            # Envoi du POST vers l'API universelle d'IndexNow
            r = await client.post("https://api.indexnow.org/indexnow", json=payload, timeout=10.0)
            if r.status_code == 200:
                logger.info("IndexNow: Indexation instantanée réussie ! (HTTP 200)")
            else:
                logger.warning("IndexNow: Retour inattendu de l'API (HTTP %d): %s", r.status_code, r.text)
        except Exception as exc:
            logger.warning("IndexNow: Échec de connexion à l'API IndexNow: %s", exc)
