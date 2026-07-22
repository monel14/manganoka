from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import MANGA_TTL_SECONDS, cache
from scraper.client import FetchError, get_html
from scraper.parser import MangaDetail, parse_manga

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/manga/{slug}", response_class=HTMLResponse)
async def manga_detail(request: Request, slug: str) -> HTMLResponse:
    # Récupérer le slug brut non-décodé (double encodage d'origine) depuis la socket HTTP pour le site source
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        if "/manga/" in raw_path:
            slug = raw_path.split("/manga/", 1)[1]

    try:
        manga = await cache.get_or_set(
            f"manga:{slug}",
            MANGA_TTL_SECONDS,
            lambda: _load_manga(slug),
        )
    except FetchError as exc:
        logger.warning("Unable to load manga %s: %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source unavailable") from exc

    if not manga["title"]:
        raise HTTPException(status_code=404, detail="Manga not found")

    return templates.TemplateResponse(
        request,
        "manga.html",
        {"request": request, "manga": manga, "slug": slug},
    )


async def _load_manga(slug: str) -> MangaDetail:
    html = await get_html(f"/manga/{slug}")
    return parse_manga(html, slug=slug)
