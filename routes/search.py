from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL_SECONDS, cache
from scraper.client import FetchError, get_html
from scraper.parser import SearchManga, parse_search

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/api/search")
def api_search(q: str = Query(default="", min_length=1)) -> JSONResponse:
    """Endpoint JSON pour le dropdown de recherche live."""
    if not q.strip():
        return JSONResponse({"results": []})

    try:
        cache_key = f"search:{q.lower().strip()}:1"
        mangas = cache.get_or_set(
            cache_key,
            HOME_TTL_SECONDS,
            lambda: _load_search(q, 1),
        )
    except FetchError as exc:
        logger.warning("Unable to search for '%s': %s", q, exc)
        return JSONResponse({"results": [], "error": str(exc)})

    return JSONResponse({
        "results": [
            {
                "title": m["title"],
                "slug": m["slug"],
                "cover": m["cover"],
                "views": m["views"],
            }
            for m in mangas[:20]
        ]
    })


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query(default="", min_length=1),
    page: int = Query(default=1, ge=1, alias="p")
) -> HTMLResponse:
    error: str | None = None
    mangas: list[SearchManga] = []

    if not q.strip():
        error = "Veuillez entrer un terme de recherche."
    else:
        try:
            cache_key = f"search:{q.lower().strip()}:{page}"
            mangas = cache.get_or_set(
                cache_key,
                HOME_TTL_SECONDS,
                lambda: _load_search(q, page),
            )
        except FetchError as exc:
            logger.warning("Unable to search for '%s' page %s: %s", q, page, exc)
            error = "Impossible de rechercher pour le moment."

    has_next_page = len(mangas) >= 20

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "request": request,
            "query": q,
            "mangas": mangas,
            "error": error,
            "current_page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if has_next_page else None,
        },
    )


def _load_search(query: str, page: int) -> list[SearchManga]:
    path = f"/search.php?manga={query}" if page == 1 else f"/search.php?manga={query}&page={page}"
    html = get_html(path)
    return parse_search(html)
