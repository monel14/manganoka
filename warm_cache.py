import asyncio
import logging
import sys
import os

# Ajouter le répertoire racine au PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from cache import cache, MANGA_TTL_SECONDS
from routes.manga import _load_manga
from scraper.client import get_html
from scraper.parser import parse_manga_list

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("warm_cache")


async def warm_cache():
    logger.info("=== [MANGAnoka] STARTING CACHE WARM-UP & SEO HARVESTING ===")
    
    # 1. Récupérer la liste des mangas depuis New Manga et Latest Manga
    all_mangas = []
    
    # New Manga (nouveaux titres)
    logger.info("Fetching 'Newest Manga' from MangaBats...")
    try:
        html = await get_html("/manga-list/new-manga")
        new_mangas = parse_manga_list(html)
        logger.info(f"Found {len(new_mangas)} new titles.")
        all_mangas.extend(new_mangas)
    except Exception as e:
        logger.error(f"Failed to fetch new manga list: {e}")
    
    # Latest Manga (derniers chapitres)
    logger.info("Fetching 'Latest Manga' from MangaBats...")
    try:
        html = await get_html("/latest-manga")
        latest_mangas = parse_manga_list(html)
        logger.info(f"Found {len(latest_mangas)} latest releases.")
        all_mangas.extend(latest_mangas)
    except Exception as e:
        logger.error(f"Failed to fetch latest manga list: {e}")
    
    # Dédupliquer par slug
    seen_slugs = set()
    mangas = []
    for manga in all_mangas:
        if manga["slug"] not in seen_slugs:
            mangas.append(manga)
            seen_slugs.add(manga["slug"])
    
    logger.info(f"Total unique mangas to cache: {len(mangas)}")
    
    if not mangas:
        logger.warning("No mangas found to cache.")
        return

    # 2. Pré-charger chaque fiche technique dans cache.db pour alimenter sitemap.xml
    harvested_count = 0
    for index, manga in enumerate(mangas):
        slug = manga["slug"]
        title = manga["title"]
        logger.info(f"[{index + 1}/{len(mangas)}] Pre-caching: '{title}' (slug: {slug})")
        
        try:
            # get_or_set va scraper et stocker les métadonnées + chapitres dans cache.db
            await cache.get_or_set(
                f"manga:{slug}",
                MANGA_TTL_SECONDS,
                lambda: _load_manga(slug)
            )
            harvested_count += 1
            
            # Pause de courtoisie de 1.5 seconde pour respecter le serveur source de MangaBats
            await asyncio.sleep(1.5)
            
        except Exception as e:
            logger.warning(f"Failed to pre-cache '{title}': {e}")

    logger.info(f"=== [MANGAnoka] WARM-UP COMPLETED ! {harvested_count} mangas successfully cached and added to sitemap.xml ===")


if __name__ == "__main__":
    asyncio.run(warm_cache())
