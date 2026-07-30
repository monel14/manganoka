import asyncio
import logging
import urllib.parse
import sys
import os
from bs4 import BeautifulSoup

# Ajouter le répertoire racine au PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from cache import cache, MANGA_TTL_SECONDS
from routes.manga import _load_manga
from scraper.client import get_html, get_http_client
from scraper.parser import parse_search

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("demonic_harvest")


async def harvest_demonic_scans():
    logger.info("=== [MANGAnoka] STARTING DEMONIC SCANS SEO HARVESTING ===")
    
    demonic_url = "https://demonicscans.org/newmangalist.php"
    logger.info(f"Scraping new titles list from: {demonic_url}")
    
    # 1. Récupérer les nouveaux titres de mangas de DemonicScans
    manga_titles = []
    client = get_http_client()
    try:
        r = await client.get(demonic_url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('h2 a'):
            title = a.get('title') or a.text.strip()
            if title and title not in manga_titles:
                manga_titles.append(title)
        logger.info(f"Successfully extracted {len(manga_titles)} hot titles from DemonicScans.")
    except Exception as e:
        logger.error(f"Failed to fetch or parse DemonicScans page: {e}")
        return

    # 2. Pour chaque titre, rechercher le slug correspondant sur notre agrégateur MangaBats
    harvested_count = 0
    for index, title in enumerate(manga_titles):
        logger.info(f"[{index + 1}/{len(manga_titles)}] Processing title: '{title}'")
        
        # Enlever les caractères spéciaux inutiles de la recherche pour maximiser le taux de réussite
        clean_query = title.replace(":", " ").replace("-", " ").replace("'", " ").strip()
        encoded_query = urllib.parse.quote(clean_query)
        
        try:
            # Effectuer la recherche sur MangaBats
            search_html = await get_html(f"/search/story/{encoded_query}")
            search_results = parse_search(search_html)
            
            if not search_results:
                logger.warning(f"No matching aggregator results found for: '{title}'")
                continue
                
            # Prendre le premier résultat de recherche (le plus pertinent)
            matched_manga = search_results[0]
            slug = matched_manga["slug"]
            matched_title = matched_manga["title"]
            
            logger.info(f"-> Found matching aggregator title: '{matched_title}' (slug: {slug})")
            
            # Pré-charger les détails et les chapitres dans cache.db (ce qui met à jour le sitemap et pings IndexNow !)
            await cache.get_or_set(
                f"manga:{slug}",
                MANGA_TTL_SECONDS,
                lambda: _load_manga(slug)
            )
            harvested_count += 1
            logger.info(f"-> Successfully cached and indexed '{matched_title}' !")
            
            # Pause de courtoisie de 1.5 seconde pour respecter l'infrastructure source
            await asyncio.sleep(1.5)
            
        except Exception as e:
            logger.warning(f"Error while harvesting '{title}': {e}")
            await asyncio.sleep(1.0)

    logger.info(f"=== [MANGAnoka] DEMONIC HARVEST COMPLETED ! {harvested_count}/{len(manga_titles)} hot titles successfully indexed and pinged! ===")


if __name__ == "__main__":
    asyncio.run(harvest_demonic_scans())
