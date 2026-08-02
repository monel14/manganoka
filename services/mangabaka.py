import os
import logging
import urllib.parse
import json
import httpx

logger = logging.getLogger(__name__)


async def fetch_alt_titles(manga_title: str) -> list[str]:
    """
    Interroge l'API publique de MangaBaka pour récupérer tous les titres alternatifs
    et romanisés d'un manga donnés pour le référencement croisé (SEO).
    """
    if not manga_title:
        return []

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
                return []
                
            payload = r.json()
            series_list = payload.get("data", [])
            if not series_list:
                logger.info("MangaBaka API: No match found for '%s'", manga_title)
                return []

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

            # Retourner sous forme de liste dédoublée propre, sans le titre principal original s'il y est déjà
            unique_list = [t for t in alt_titles if t.lower() != manga_title.lower()]
            logger.info("MangaBaka API: Successfully retrieved %d alt titles for '%s'", len(unique_list), manga_title)
            return unique_list

        except Exception as exc:
            logger.warning("MangaBaka API: Failed to fetch alt titles for '%s': %s", manga_title, exc)
            return []
