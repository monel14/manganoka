from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL_SECONDS, cache
from scraper.client import FetchError, get_html
from scraper.parser import HomeManga, parse_home

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, list_page: int = Query(default=1, ge=1, alias="list")) -> HTMLResponse:
    error: str | None = None

    try:
        mangas = await cache.get_or_set(
            f"home:lastupdates:{list_page}",
            HOME_TTL_SECONDS,
            lambda: _load_home(list_page),
        )
    except FetchError as exc:
        logger.warning("Unable to load homepage data page %s: %s", list_page, exc)
        mangas = []
        error = "Impossible de charger les dernières sorties pour le moment."

    has_next_page = bool(mangas)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "mangas": mangas,
            "error": error,
            "current_page": list_page,
            "previous_page": list_page - 1 if list_page > 1 else None,
            "next_page": list_page + 1 if has_next_page else None,
        },
    )


async def _load_home(page: int) -> list[HomeManga]:
    path = "/lastupdates.php" if page == 1 else f"/lastupdates.php?list={page}"
    html = await get_html(path)
    return parse_home(html)
