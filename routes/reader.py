from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import CHAPTER_TTL_SECONDS, MANGA_TTL_SECONDS, cache
from scraper.client import FetchError, get_html
from scraper.parser import ChapterLink, ChapterPage, MangaDetail, parse_chapter, parse_manga

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def get_chapter_page(slug: str, chapter: str, manga: MangaDetail | None = None) -> ChapterPage:
    """
    Récupère une page de chapitre depuis le cache ou la charge.
    
    Args:
        slug: Slug du manga
        chapter: Numéro du chapitre
        manga: Données du manga déjà chargées (optionnel, pour éviter un double chargement)
    """
    if manga is None:
        manga = await cache.get_or_set(
            f"manga:{slug}",
            MANGA_TTL_SECONDS,
            lambda: _load_manga(slug),
        )
    
    return await cache.get_or_set(
        f"chapter:{slug}:{chapter}",
        CHAPTER_TTL_SECONDS,
        lambda: _load_chapter_from_manga(manga, chapter),
    )


@router.get("/read/{slug}/{chapter}", response_class=HTMLResponse)
async def read_chapter(request: Request, slug: str, chapter: str) -> HTMLResponse:
    # Récupérer le slug et le chapitre bruts non-décodés (double encodage) depuis la socket HTTP pour le site source
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        if "/read/" in raw_path:
            parts = raw_path.split("/read/", 1)[1].split("/")
            if len(parts) >= 2:
                slug = parts[0]
                chapter = parts[1]

    try:
        manga = await cache.get_or_set(
            f"manga:{slug}",
            MANGA_TTL_SECONDS,
            lambda: _load_manga(slug),
        )
        # Passer le manga déjà chargé pour éviter un double fetch
        page = await get_chapter_page(slug, chapter, manga=manga)
    except FetchError as exc:
        logger.warning("Unable to load chapter %s/%s: %s", slug, chapter, exc)
        raise HTTPException(status_code=502, detail="Source indisponible") from exc

    if not page["images"]:
        raise HTTPException(status_code=404, detail="Chapitre introuvable")

    previous_chapter, next_chapter = _chapter_neighbors(manga["chapters"], chapter)

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


async def _load_chapter(slug: str, chapter: str) -> ChapterPage:
    return await get_chapter_page(slug, chapter)


async def _load_chapter_from_manga(manga: MangaDetail, chapter: str) -> ChapterPage:
    source_url = _find_chapter_url(manga, chapter)
    html = await get_html(source_url)
    return parse_chapter(html, title=manga["title"], chapter=chapter)


async def _load_manga(slug: str) -> MangaDetail:
    html = await get_html(f"/manga/{slug}")
    return parse_manga(html, slug=slug)


def _find_chapter_url(manga: MangaDetail, chapter: str) -> str:
    for item in manga["chapters"]:
        if str(item["number"]) == str(chapter):
            return item["url"]
    raise HTTPException(status_code=404, detail="Chapitre introuvable")


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
