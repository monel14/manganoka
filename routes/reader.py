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

from cache import CHAPTER_TTL_SECONDS, MANGA_TTL_SECONDS, cache
from models import ChapterLink, ChapterPage, MangaDetail
from services.indexnow import ping_indexnow
from services.google_indexing import ping_google_indexing
from services.phenix_scans import get_phenix_api

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def get_chapter_page(slug: str, chapter: str, manga: MangaDetail | None = None) -> ChapterPage:
    if manga is None:
        manga = await cache.get_or_set(
            f"manga:fr:{slug}",
            MANGA_TTL_SECONDS,
            lambda: get_phenix_api().get_manga_detail(slug),
        )

    return await cache.get_or_set(
        f"chapter:fr:{slug}:{chapter}",
        CHAPTER_TTL_SECONDS,
        lambda: get_phenix_api().get_chapter_images(slug, chapter),
    )


@router.get("/read/{slug}/{chapter}", response_class=HTMLResponse)
async def read_chapter(request: Request, slug: str, chapter: str, background_tasks: BackgroundTasks) -> HTMLResponse:
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        if "/read/" in raw_path:
            parts = raw_path.split("/read/", 1)[1].split("/")
            if len(parts) >= 2:
                slug = parts[0]
                chapter = parts[1]

    api = get_phenix_api()
    try:
        manga = await cache.get_or_set(
            f"manga:fr:{slug}",
            MANGA_TTL_SECONDS,
            lambda: api.get_manga_detail(slug),
        )

        if not manga or not isinstance(manga, dict) or not manga.get("chapters"):
            logger.warning("Manga Reader Cache: Corrupt manga cache detected for %s. Force re-scraping...", slug)
            manga = await api.get_manga_detail(slug)
            if manga:
                await cache.get_or_set(f"manga:fr:{slug}", MANGA_TTL_SECONDS, lambda: manga)
            else:
                raise HTTPException(status_code=404, detail="Manga introuvable")

        images = await cache.get_or_set(
            f"chapter:fr:{slug}:{chapter}",
            CHAPTER_TTL_SECONDS,
            lambda: api.get_chapter_images(slug, chapter),
        )
        page: ChapterPage = {"title": manga["title"], "images": images}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Unable to load chapter %s/%s: %s", slug, chapter, exc)
        raise HTTPException(status_code=502, detail="Source indisponible") from exc

    if not page["images"]:
        raise HTTPException(status_code=404, detail="Chapitre introuvable")

    previous_chapter, next_chapter = _chapter_neighbors(manga["chapters"], chapter)

    if not _is_bot_request(request):
        background_tasks.add_task(ping_indexnow, [f"/fr/read/{slug}/{chapter}"])
        background_tasks.add_task(ping_google_indexing, [f"/fr/read/{slug}/{chapter}"])
    else:
        logger.debug("Bot detected, skipping indexing ping for /read/%s/%s", slug, chapter)

    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "request": request,
            "page": page,
            "slug": slug,
            "chapter": chapter,
            "manga": manga,
            "chapters": manga["chapters"],
            "previous_chapter": previous_chapter,
            "next_chapter": next_chapter,
        },
    )


def _chapter_neighbors(
    chapters: list[ChapterLink],
    current: str,
) -> tuple[ChapterLink | None, ChapterLink | None]:
    for index, item in enumerate(chapters):
        if str(item["number"]) != str(current):
            continue
        previous_chapter = chapters[index + 1] if index + 1 < len(chapters) else None
        next_chapter = chapters[index - 1] if index > 0 else None
        return previous_chapter, next_chapter
    return None, None
