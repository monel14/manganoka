from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

def _is_bot_request(request: Request) -> bool:
    user_agent = request.headers.get("user-agent", "").lower()
    bot_patterns = [
        'bot', 'crawler', 'spider', 'crawling', 'scraper',
        'googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider',
        'yandexbot', 'sogou', 'exabot', 'facebot', 'ia_archiver',
        'semrush', 'ahrefs', 'majestic', 'mj12bot', 'dotbot'
    ]
    return any(pattern in user_agent for pattern in bot_patterns)

from cache import MANGA_TTL_SECONDS, cache
from models import MangaDetail
from services.indexnow import ping_indexnow
from services.google_indexing import ping_google_indexing
from services.phenix_scans import get_phenix_api

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/manga/{slug}", response_class=HTMLResponse)
async def manga_detail(request: Request, slug: str, background_tasks: BackgroundTasks) -> HTMLResponse:
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        if "/manga/" in raw_path:
            slug = raw_path.split("/manga/", 1)[1]

    api = get_phenix_api()
    try:
        manga = await cache.get_or_set(
            f"manga:fr:{slug}",
            MANGA_TTL_SECONDS,
            lambda: api.get_manga_detail(slug),
        )

        if not manga or not isinstance(manga, dict) or not manga.get("title"):
            logger.warning("Manga Cache: Corrupt cache entry detected for %s. Force re-scraping...", slug)
            manga = await api.get_manga_detail(slug)
            if manga:
                await cache.get_or_set(f"manga:fr:{slug}", MANGA_TTL_SECONDS, lambda: manga)

        if not manga or not manga.get("title"):
            raise HTTPException(status_code=404, detail="Manga non trouvé")

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Unable to load manga %s: %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source indisponible") from exc

    related_mangas = _get_related_mangas(slug, limit=6)

    if not _is_bot_request(request):
        background_tasks.add_task(ping_indexnow, [f"/fr/manga/{slug}"])
        background_tasks.add_task(ping_google_indexing, [f"/fr/manga/{slug}"])
    else:
        logger.debug("Bot detected, skipping indexing ping for /manga/%s", slug)

    return templates.TemplateResponse(
        request,
        "manga.html",
        {"request": request, "manga": manga, "slug": slug, "related_mangas": related_mangas},
    )


def _cache_get(key: str) -> dict | None:
    import sqlite3
    import json

    _CACHE_DB = Path(__file__).resolve().parent.parent / "cache.db"
    try:
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as conn:
            row = conn.execute("SELECT data FROM cache WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _get_related_mangas(current_slug: str, limit: int = 6) -> list[dict]:
    import random

    current_manga = _cache_get(f"manga:fr:{current_slug}")
    if current_manga and isinstance(current_manga, dict):
        genres_list = current_manga.get("genres") or []
        current_genres = set(genres_list) if isinstance(genres_list, list) else set()
    else:
        current_genres = set()

    manga_keys = cache.get_keys_by_prefix("manga:fr:")
    all_slugs = [k.split("manga:fr:", 1)[1] for k in manga_keys if "manga:fr:" in k and k.split("manga:fr:", 1)[1] != current_slug]

    scored_mangas = []
    for slug in all_slugs:
        manga = _cache_get(f"manga:fr:{slug}")
        if not manga or not isinstance(manga, dict):
            continue
        genres_list = manga.get("genres") or []
        manga_genres = set(genres_list) if isinstance(genres_list, list) else set()
        score = len(current_genres & manga_genres)
        if score > 0:
            scored_mangas.append((score, slug, manga))

    if len(scored_mangas) < limit:
        random.shuffle(all_slugs)
        for slug in all_slugs:
            if len(scored_mangas) >= limit * 2:
                break
            if not any(s[1] == slug for s in scored_mangas):
                manga = _cache_get(f"manga:fr:{slug}")
                if manga and isinstance(manga, dict):
                    scored_mangas.append((0, slug, manga))

    scored_mangas.sort(key=lambda x: x[0], reverse=True)
    top_candidates = scored_mangas[:limit * 2] if len(scored_mangas) > limit else scored_mangas
    random.shuffle(top_candidates)
    selected = top_candidates[:limit]

    return [
        {
            "title": manga.get("title", ""),
            "slug": slug,
            "cover": manga.get("cover", ""),
            "latest_chapter": manga.get("chapters", [{}])[0].get("number", "1") if manga.get("chapters") else "1",
            "genres": manga.get("genres", [])[:3],
        }
        for _, slug, manga in selected
    ]
