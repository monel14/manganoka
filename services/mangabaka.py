import os
import logging
import urllib.parse
import json
import httpx

logger = logging.getLogger(__name__)


async def fetch_mangabaka_data(manga_title: str) -> dict:
    """
    Interroge l'API publique de MangaBaka pour récupérer à la fois les titres alternatifs (SEO)
    et l'URL directe de couverture JPEG haute qualité.
    
    Returns:
        dict: {"alt_titles": [...], "cover_url": "https://..."}
    """
    result = {"alt_titles": [], "cover_url": None}
    if not manga_title:
        return result

    # Enlever les numéros de chapitre éventuels pour la recherche globale
    clean_query = manga_title.split(":")[0].split("·")[0].strip()
    encoded_query = urllib.parse.quote(clean_query)
    
    url = f"https://api.mangabaka.org/v1/series/search?q={encoded_query}"
    logger.info("MangaBaka API: Querying %s", url)

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers={"User-Agent": "MangaNoka/1.0 (SEO Agent)"}, timeout=8.0)
            if r.status_code != 200:
                logger.warning("MangaBaka API returned HTTP %d for %s", r.status_code, manga_title)
                return result
                
            payload = r.json()
            series_list = payload.get("data", [])
            if not series_list:
                logger.info("MangaBaka API: No match found for '%s'", manga_title)
                return result

            # Sélectionner le premier résultat (le plus pertinent)
            series = series_list[0]
            alt_titles = set()

            # Ajouter le titre principal, natif et romanisé
            if series.get("title"):
                alt_titles.add(series["title"])
            if series.get("native_title"):
                alt_titles.add(series["native_title"])
            if series.get("romanized_title"):
                alt_titles.add(series["romanized_title"])

            # Ajouter tous les titres secondaires
            secondary = series.get("secondary_titles", {})
            for lang, titles in secondary.items():
                for t in titles:
                    if isinstance(t, dict) and t.get("title"):
                        alt_titles.add(t["title"])

            # Récupérer l'URL directe de couverture JPEG haute qualité (depuis le format raw)
            cover_url = None
            cover_data = series.get("cover", {})
            if isinstance(cover_data, dict):
                raw_cover = cover_data.get("raw", {})
                if isinstance(raw_cover, dict) and raw_cover.get("url"):
                    cover_url = raw_cover["url"]
                else:
                    # Fallback sur x350 si le format raw n'est pas disponible
                    x350_cover = cover_data.get("x350", {})
                    if isinstance(x350_cover, dict) and x350_cover.get("x1"):
                        cover_url = x350_cover["x1"]

            result["alt_titles"] = [t for t in alt_titles if t.lower() != manga_title.lower()]
            result["cover_url"] = cover_url
            
            logger.info("MangaBaka API: Successfully retrieved %d alt titles and cover URL for '%s'", len(result["alt_titles"]), manga_title)
            return result

        except Exception as exc:
            logger.warning("MangaBaka API: Failed to fetch data for '%s': %s", manga_title, exc)
            return result
