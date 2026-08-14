from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Utilitaire pour détecter les bots
def _is_bot_request(request: Request) -> bool:
    """Détecte si la requête provient d'un bot/crawler."""
    user_agent = request.headers.get("user-agent", "").lower()
    bot_patterns = [
        'bot', 'crawler', 'spider', 'crawling', 'scraper',
        'googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider',
        'yandexbot', 'sogou', 'exabot', 'facebot', 'ia_archiver',
        'semrush', 'ahrefs', 'majestic', 'mj12bot', 'dotbot'
    ]
    return any(pattern in user_agent for pattern in bot_patterns)

from cache import CHAPTER_TTL_SECONDS, CHAPTER_TTL_SECONDS, MANGA_TTL_SECONDS, cache
from scraper.client import FetchError, NotFoundError, get_html
from scraper.parser import ChapterLink, ChapterPage, MangaDetail, parse_chapter, parse_manga
from services.indexnow import ping_indexnow
from services.google_indexing import ping_google_indexing

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
async def read_chapter(request: Request, slug: str, chapter: str, background_tasks: BackgroundTasks) -> HTMLResponse:
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
        
        # SÉCURITÉ ACTIVE (Self-Healing) : Si la fiche manga est corrompue, on la répare et la re-scrape en direct !
        if not manga or not isinstance(manga, dict) or not manga.get("chapters"):
            logger.warning("Manga Reader Cache: Corrupt manga cache detected for %s. Force re-scraping...", slug)
            try:
                manga = await _load_manga(slug)
                await cache.get_or_set(f"manga:{slug}", MANGA_TTL_SECONDS, lambda: manga)
            except Exception as e:
                logger.error("Manga Reader Cache: Failed to self-heal corrupt cache for %s: %s", slug, e)
                raise HTTPException(status_code=404, detail="Manga not found")
            
        # Passer le manga déjà chargé pour éviter un double fetch
        page = await get_chapter_page(slug, chapter, manga=manga)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chapter not found") from exc
    except FetchError as exc:
        logger.warning("Unable to load chapter %s/%s: %s", slug, chapter, exc)
        raise HTTPException(status_code=502, detail="Source unavailable") from exc

    if not page["images"]:
        raise HTTPException(status_code=404, detail="Chapter not found")

    previous_chapter, next_chapter = _chapter_neighbors(manga["chapters"], chapter)

    # Indexation + Webhook : Ne déclencher que pour les vrais utilisateurs (pas les bots)
    if not _is_bot_request(request):
        background_tasks.add_task(ping_indexnow, [f"/read/{slug}/{chapter}"])
        background_tasks.add_task(ping_google_indexing, [f"/read/{slug}/{chapter}"])
        # Webhook Make.com : déclenché ici dans la route, pas dans le loader de cache
        # Ainsi il s'exécute à chaque visite, protégé par l'anti-doublon GUID
        background_tasks.add_task(
            _trigger_webhook,
            manga,
            slug,
            chapter,
        )
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


async def _trigger_webhook(manga: MangaDetail, slug: str, chapter: str) -> None:
    """
    Déclenche le webhook Make.com depuis la route (pas depuis le loader de cache).
    Protégé par l'anti-doublon GUID → s'exécute à chaque visite mais n'envoie qu'une seule fois par chapitre.
    """
    try:
        from services.webhook_pusher import push_to_make_webhook
        chapters = manga.get("chapters", [])
        await push_to_make_webhook(
            manga_title=manga["title"],
            slug=slug,
            latest_ch_num=chapter,
            raw_desc=manga.get("description"),
            cover=manga.get("cover"),
            bakacover=manga.get("bakacover"),
            all_chapters=chapters,
            is_new_manga=False,
        )
    except Exception as exc:
        logger.warning("Failed to trigger Make Webhook for reader %s/%s: %s", slug, chapter, exc)


async def _load_chapter(slug: str, chapter: str) -> ChapterPage:
    return await get_chapter_page(slug, chapter)


async def _load_chapter_from_manga(manga: MangaDetail, chapter: str) -> ChapterPage:
    source_url = _find_chapter_url(manga, chapter)
    html = await get_html(source_url)
    return parse_chapter(html, title=manga["title"], chapter=chapter)


async def _load_manga(slug: str) -> MangaDetail:
    html = await get_html(f"/manga/{slug}")
    
    # Récupérer la liste des chapitres depuis l'API officielle de MangaBats
    chapters_json_url = f"https://www.mangabats.com/api/manga/{slug}/chapters?limit=10000"
    from scraper.client import get_http_client
    client = get_http_client()
    try:
        r = await client.get(chapters_json_url)
        r.raise_for_status()
        chapters_data = r.json()
    except Exception as exc:
        logger.warning("Failed to fetch chapters JSON for %s: %s", slug, exc)
        chapters_data = {}
        
    manga_detail = parse_manga(html, slug=slug, chapters_data=chapters_data)
    try:
        from services.mangabaka import fetch_mangabaka_data
        mb_data = await fetch_mangabaka_data(manga_detail["title"])
        manga_detail["alt_titles"] = mb_data.get("alt_titles", [])
        manga_detail["bakacover"] = mb_data.get("cover_url")
    except Exception as exc:
        logger.warning("Failed to enrich manga %s with MangaBaka data: %s", slug, exc)
        manga_detail["alt_titles"] = []
        manga_detail["bakacover"] = None
        
    # Pré-charger la couverture d'image dans le cache pour éviter tout 404 sur Pinterest / Buffer / RSS
    target_cover = manga_detail.get("bakacover") or manga_detail.get("cover")
    if target_cover:
        try:
            from routes.images import get_image_cache_service
            service = get_image_cache_service()
            await service.get_or_cache_image(target_cover, bypass_validation=True)
            logger.info("Manga Cache: Cover image pre-cached successfully for %s", slug)
        except Exception as exc:
            logger.warning("Manga Cache: Failed to pre-cache cover image: %s", exc)

    return manga_detail


def _find_chapter_url(manga: MangaDetail, chapter: str) -> str:
    for item in manga["chapters"]:
        if str(item["number"]) == str(chapter):
            return item["url"]
    raise HTTPException(status_code=404, detail="Chapter not found")


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
