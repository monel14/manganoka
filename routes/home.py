from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL_SECONDS, cache
import os
from scraper.client import FetchError, get_html
from scraper.parser import HomeManga, parse_home, parse_popular, parse_popular_sidebar

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, list_page: int = Query(default=1, ge=1, alias="list")) -> HTMLResponse:
    error: str | None = None

    try:
        data = await cache.get_or_set(
            f"home:lastupdates:{list_page}",
            HOME_TTL_SECONDS,
            lambda: _load_home(list_page),
        )
        mangas = data.get("mangas", [])
        popular = data.get("popular", [])
        popular_sidebar = data.get("popular_sidebar", [])
    except FetchError as exc:
        logger.warning("Unable to load homepage data page %s: %s", list_page, exc)
        mangas = []
        popular = []
        popular_sidebar = []
        error = "Unable to load latest releases at the moment."

    has_next_page = bool(mangas)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "mangas": mangas,
            "popular": popular,
            "popular_sidebar": popular_sidebar,
            "error": error,
            "current_page": list_page,
            "previous_page": list_page - 1 if list_page > 1 else None,
            "next_page": list_page + 1 if has_next_page else None,
        },
    )


async def _load_home(page: int) -> dict:
    if page == 1:
        path = "/"
    else:
        path = f"/manga-list/latest-manga?page={page}"
    html = await get_html(path)
    return {
        "mangas": parse_home(html),
        "popular": parse_popular(html) if page == 1 else [],
        "popular_sidebar": parse_popular_sidebar(html) if page == 1 else [],
    }


@router.get("/sitemap.xml")
def sitemap() -> Response:
    """Génère un sitemap XML dynamique basé sur les mangas actuellement en cache."""
    base_url = os.environ.get("BASE_URL", "https://manganoka.xyz").rstrip("/")
    
    # Récupérer toutes les clés de manga du cache (ex: 'manga:Dan%252C-the-Bat...')
    manga_keys = cache.get_keys_by_prefix("manga:")
    slugs = []
    for key in manga_keys:
        if ":" in key:
            slugs.append(key.split(":", 1)[1])

    # Génération du contenu XML
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    
    # 1. URL de la page d'accueil
    xml_lines.append("    <url>")
    xml_lines.append(f"        <loc>{base_url}/</loc>")
    xml_lines.append("        <changefreq>daily</changefreq>")
    xml_lines.append("        <priority>1.0</priority>")
    xml_lines.append("    </url>")
    
    # 2. URLs de tous les mangas en cache
    for slug in slugs:
        xml_lines.append("    <url>")
        xml_lines.append(f"        <loc>{base_url}/manga/{slug}</loc>")
        xml_lines.append("        <changefreq>weekly</changefreq>")
        xml_lines.append("        <priority>0.8</priority>")
        xml_lines.append("    </url>")
        
    xml_lines.append("</urlset>")
    xml_content = "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml")


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    """Sert la page d'historique de lecture dédiée."""
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "request": request,
        },
    )
