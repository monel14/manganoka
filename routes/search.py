from __future__ import annotations

import logging
import re
import unicodedata
from typing import TypedDict
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL_SECONDS, cache
from services.phenix_scans import get_phenix_api

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def normalize_query(query: str) -> str:
    """
    Normalise une requête de recherche pour améliorer le taux de hit du cache.
    
    Transformations appliquées :
    - Suppression des accents (é → e, à → a, etc.)
    - Conversion en minuscules
    - Suppression des espaces multiples/tabulations
    - Trim des espaces de début/fin
    
    Examples:
        >>> normalize_query("  Naruto  ")
        'naruto'
        >>> normalize_query("Café Français")
        'cafe francais'
        >>> normalize_query("ONE    PIECE")
        'one piece'
    """
    if not query:
        return ""
    
    # Suppression des accents (NFD = décomposition, puis on garde que ASCII)
    query = unicodedata.normalize('NFD', query)
    query = ''.join(char for char in query if unicodedata.category(char) != 'Mn')
    
    # Conversion en minuscules
    query = query.lower()
    
    # Remplacement des espaces multiples/tabs par un seul espace
    query = re.sub(r'\s+', ' ', query)
    
    # Trim
    query = query.strip()
    
    return query


class SearchManga(TypedDict):
    title: str
    slug: str
    cover: str
    latest_chapter: str
    views: str


@router.get("/api/search")
async def api_search(q: str = Query(default="", min_length=1)) -> JSONResponse:
    """Endpoint JSON pour le dropdown de recherche live."""
    normalized_q = normalize_query(q)
    if not normalized_q:
        return JSONResponse({"results": []})

    try:
        cache_key = f"search:fr:{normalized_q}:1"
        result = await cache.get_or_set(
            cache_key,
            HOME_TTL_SECONDS,
            lambda: _load_search(normalized_q, 1),
        )
        mangas = result if isinstance(result, list) else []
    except Exception as exc:
        logger.warning("Impossible de chercher '%s': %s", q, exc)
        return JSONResponse({"results": [], "error": str(exc)})

    return JSONResponse({
        "results": [
            {
                "title": m["title"],
                "slug": m["slug"],
                "cover": m["cover"],
                "views": m.get("views", ""),
                "author": m.get("author", ""),
                "latest_chapter": m.get("latest_chapter", ""),
            }
            for m in mangas[:20]
        ]
    })


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(default="", min_length=1),
    page: int = Query(default=1, ge=1, alias="p")
) -> HTMLResponse:
    error: str | None = None
    mangas: list[SearchManga] = []
    normalized_q = normalize_query(q)

    if not normalized_q:
        error = "Veuillez entrer une requête de recherche."
    else:
        try:
            cache_key = f"search:fr:{normalized_q}:{page}"
            result = await cache.get_or_set(
                cache_key,
                HOME_TTL_SECONDS,
                lambda: _load_search(normalized_q, page),
            )
            mangas = result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("Impossible de chercher '%s' page %s: %s", q, page, exc)
            error = "Impossible d'effectuer la recherche pour le moment."

    has_next_page = len(mangas) >= 20
    previous_page_url = f"/search?q={q}&p={page - 1}" if page > 1 else None
    next_page_url = f"/search?q={q}&p={page + 1}" if has_next_page else None

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "request": request,
            "query": q,
            "mangas": mangas,
            "error": error,
            "current_page": page,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
        },
    )


async def _load_search(query: str, page: int) -> list[SearchManga]:
    """Recherche les mangas via l'API Phenix Scans.
    
    L'API Phenix Scans expose /api/manga?search=<query>.
    On retourne une liste compatible SearchManga.
    """
    try:
        api = get_phenix_api()
        mangas_raw, _ = await api.get_latest_mangas(page=page, limit=20)
        # Filtrer par correspondance de titre (recherche locale côté client)
        q_lower = query.lower()
        results: list[SearchManga] = []
        for m in mangas_raw:
            if q_lower in m.get("title", "").lower():
                chapters = m.get("chapters", [])
                results.append({
                    "title": m.get("title", ""),
                    "slug": m.get("slug", ""),
                    "cover": m.get("cover", ""),
                    "latest_chapter": chapters[0].get("title", "") if chapters else "",
                    "views": "",
                })
        return results
    except Exception as exc:
        logger.warning("Échec de la recherche Phenix Scans pour '%s': %s", query, exc)
        return []
