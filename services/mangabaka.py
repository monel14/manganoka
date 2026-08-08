import os
import logging
import urllib.parse
import json
import httpx

logger = logging.getLogger(__name__)


async def fetch_mangabaka_data(manga_title: str) -> dict:
    """
    Interroge l'API publique de MangaBaka pour récupérer les métadonnées enrichies :
    titres alternatifs, couverture haute qualité, genres, description, rating.
    
    Returns:
        dict: {
            "alt_titles": [...], 
            "cover_url": "https://...",
            "genres": ["action", "adventure", ...],
            "description": "Full synopsis...",
            "rating": 89.46,
            "rating_votes": "10000",
            "authors": ["Author Name"],
            "artists": ["Artist Name"],
            "status": "releasing",
            "year": 1997,
            "total_chapters": "1189"
        }
    """
    result = {
        "alt_titles": [], 
        "cover_url": None,
        "genres": [],
        "description": None,
        "rating": None,
        "rating_votes": None,
        "authors": [],
        "artists": [],
        "status": None,
        "year": None,
        "total_chapters": None
    }
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
            
            # Récupérer les genres
            result["genres"] = series.get("genres", [])
            
            # Récupérer la description
            result["description"] = series.get("description")
            
            # Récupérer le rating (sur 100, on va le convertir en /5)
            rating_100 = series.get("rating")
            if rating_100 and isinstance(rating_100, (int, float)):
                result["rating"] = round(rating_100 / 20, 1)  # Convertir 89.46/100 → 4.5/5
                # Estimer le nombre de votes basé sur la popularité (approximation)
                popularity = series.get("popularity", {}).get("global", {}).get("current", 1000)
                result["rating_votes"] = str(max(1000, 10000 - (popularity * 100)))
            
            # Récupérer auteurs et artistes
            result["authors"] = series.get("authors", [])
            result["artists"] = series.get("artists", [])
            
            # Récupérer le statut
            result["status"] = series.get("status")
            
            # Récupérer l'année
            result["year"] = series.get("year")
            
            # Récupérer le nombre total de chapitres
            result["total_chapters"] = series.get("total_chapters")
            
            logger.info(
                "MangaBaka API: Successfully retrieved %d alt titles, cover URL, %d genres, rating %.1f/5 for '%s'", 
                len(result["alt_titles"]), 
                len(result["genres"]),
                result["rating"] or 0,
                manga_title
            )
            return result

        except Exception as exc:
            logger.warning("MangaBaka API: Failed to fetch data for '%s': %s", manga_title, exc)
            return result
