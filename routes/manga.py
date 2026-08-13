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

from cache import MANGA_TTL_SECONDS, cache
from scraper.client import FetchError, NotFoundError, get_html, get_http_client
from scraper.parser import MangaDetail, parse_manga
from services.indexnow import ping_indexnow
from services.google_indexing import ping_google_indexing

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/manga/{slug}", response_class=HTMLResponse)
async def manga_detail(request: Request, slug: str, background_tasks: BackgroundTasks) -> HTMLResponse:
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
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Manga not found") from exc
    except FetchError as exc:
        logger.warning("Unable to load manga %s: %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source unavailable") from exc

    # SÉCURITÉ ACTIVE (Self-Healing) : Si l'entrée de cache est corrompue ou None, on la répare et la re-scrape en direct !
    if not manga or not isinstance(manga, dict) or not manga.get("title"):
        logger.warning("Manga Cache: Corrupt cache entry detected for %s. Force re-scraping...", slug)
        try:
            manga = await _load_manga(slug)
            # Enregistrer la nouvelle version saine dans le cache
            await cache.get_or_set(f"manga:{slug}", MANGA_TTL_SECONDS, lambda: manga)
        except Exception as e:
            logger.error("Manga Cache: Failed to self-heal corrupt cache for %s: %s", slug, e)
            raise HTTPException(status_code=404, detail="Manga not found")

    # Récupérer des mangas similaires depuis le cache (Related Manga)
    related_mangas = _get_related_mangas(slug, limit=6)

    # Indexation : Ne déclencher que pour les vrais utilisateurs (pas les bots)
    # pour économiser le quota Google Indexing API (200 requêtes/jour)
    if not _is_bot_request(request):
        # Déclencher l'indexation instantanée sur Bing/Yandex via IndexNow en arrière-plan
        background_tasks.add_task(ping_indexnow, [f"/manga/{slug}"])
        # Notifier également Google via son API d'indexation officielle
        background_tasks.add_task(ping_google_indexing, [f"/manga/{slug}"])
    else:
        logger.debug("Bot detected, skipping indexing ping for /manga/%s", slug)

    return templates.TemplateResponse(
        request,
        "manga.html",
        {"request": request, "manga": manga, "slug": slug, "related_mangas": related_mangas},
    )


async def _load_manga(slug: str) -> MangaDetail:
    html = await get_html(f"/manga/{slug}")
    
    # Récupérer la liste des chapitres depuis l'API officielle de MangaBats
    chapters_json_url = f"https://www.mangabats.com/api/manga/{slug}/chapters?limit=10000"
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
        manga_detail["genres"] = mb_data.get("genres", [])
        manga_detail["baka_rating"] = mb_data.get("rating")
        manga_detail["baka_rating_votes"] = mb_data.get("rating_votes")
        manga_detail["baka_description"] = mb_data.get("description")
        manga_detail["baka_authors"] = mb_data.get("authors", [])
        manga_detail["baka_status"] = mb_data.get("status")
        manga_detail["baka_year"] = mb_data.get("year")
    except Exception as exc:
        logger.warning("Failed to enrich manga %s with MangaBaka data: %s", slug, exc)
        manga_detail["alt_titles"] = []
        manga_detail["bakacover"] = None
        manga_detail["genres"] = []
        manga_detail["baka_rating"] = None
        manga_detail["baka_rating_votes"] = None
        manga_detail["baka_description"] = None
        manga_detail["baka_authors"] = []
        manga_detail["baka_status"] = None
        manga_detail["baka_year"] = None

    # Pré-charger la couverture d'image (MangaBaka ou d'origine) dans le cache pour éviter tout 404 sur Pinterest / Buffer / RSS
    target_cover = manga_detail.get("bakacover") or manga_detail.get("cover")
    if target_cover:
        try:
            from routes.images import get_image_cache_service
            service = get_image_cache_service()
            await service.get_or_cache_image(target_cover, bypass_validation=True)
            logger.info("Manga Cache: Cover image pre-cached successfully for %s", slug)
        except Exception as exc:
            logger.warning("Manga Cache: Failed to pre-cache cover image: %s", exc)
        
    # Déclencher la publication en temps réel vers le Webhook Make.com
    # STRATÉGIE DOUBLE:
    # 1. Si NOUVEAU manga → Publier le premier chapitre pour annoncer sa disponibilité
    # 2. Si manga existant → Publier UNIQUEMENT le dernier chapitre (anti-spam)
    try:
        from services.webhook_pusher import push_to_make_webhook, is_new_manga_detection
        
        chapters = manga_detail.get("chapters", [])
        
        # Détecter si c'est un nouveau manga
        is_new = is_new_manga_detection(slug)
        
        if is_new:
            # NOUVEAU MANGA: Publier le premier chapitre pour l'annoncer
            first_chapter = chapters[-1] if chapters else None
            if first_chapter:
                first_ch_num = str(first_chapter.get("number", "1"))
                logger.info("Nouveau manga détecté: %s. Publication du premier chapitre (%s).", slug, first_ch_num)
                await push_to_make_webhook(
                    manga_title=manga_detail["title"],
                    slug=slug,
                    latest_ch_num=first_ch_num,
                    raw_desc=manga_detail.get("description"),
                    cover=manga_detail.get("cover"),
                    bakacover=manga_detail.get("bakacover"),
                    all_chapters=chapters,
                    is_new_manga=True  # Flag pour message spécial "NEW MANGA"
                )
        
        # Toujours publier le dernier chapitre (pour mangas existants ou nouveaux)
        latest_ch_num = str(chapters[0].get("number", "1")) if chapters else "1"
        await push_to_make_webhook(
            manga_title=manga_detail["title"],
            slug=slug,
            latest_ch_num=latest_ch_num,
            raw_desc=manga_detail.get("description"),
            cover=manga_detail.get("cover"),
            bakacover=manga_detail.get("bakacover"),
            all_chapters=chapters,
            is_new_manga=False
        )
    except Exception as exc:
        logger.warning("Failed to trigger Make Webhook for manga %s: %s", slug, exc)

    return manga_detail


def _cache_get(key: str) -> dict | None:
    """Lecture directe du cache SQLite sans déclencher de scrape."""
    import sqlite3
    import json
    from pathlib import Path
    
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
    """Récupère des mangas similaires basés sur les genres partagés pour un maillage interne intelligent."""
    import random
    
    # Récupérer le manga actuel pour obtenir ses genres
    current_manga = _cache_get(f"manga:{current_slug}")
    if current_manga and isinstance(current_manga, dict):
        genres_list = current_manga.get("genres") or []
        current_genres = set(genres_list) if isinstance(genres_list, list) else set()
    else:
        current_genres = set()
    
    # Récupérer tous les slugs de mangas en cache
    manga_keys = cache.get_keys_by_prefix("manga:")
    all_slugs = [k.split(":", 1)[1] for k in manga_keys if ":" in k and k.split(":", 1)[1] != current_slug]
    
    # Scorer les mangas par genres partagés
    scored_mangas = []
    for slug in all_slugs:
        manga = _cache_get(f"manga:{slug}")
        if not manga or not isinstance(manga, dict):
            continue
            
        genres_list = manga.get("genres") or []
        manga_genres = set(genres_list) if isinstance(genres_list, list) else set()
        shared_genres = current_genres & manga_genres
        score = len(shared_genres)
        
        # Bonus si au moins 1 genre partagé
        if score > 0:
            scored_mangas.append((score, slug, manga))
    
    # Si pas assez de mangas avec genres partagés, compléter avec des random
    if len(scored_mangas) < limit:
        random.shuffle(all_slugs)
        for slug in all_slugs:
            if len(scored_mangas) >= limit * 2:  # Pool plus large pour la sélection
                break
            if not any(s[1] == slug for s in scored_mangas):
                manga = _cache_get(f"manga:{slug}")
                if manga and isinstance(manga, dict):
                    scored_mangas.append((0, slug, manga))
    
    # Trier par score décroissant, puis randomiser dans chaque groupe de score
    scored_mangas.sort(key=lambda x: x[0], reverse=True)
    
    # Prendre les top {limit} avec un peu de randomisation
    top_candidates = scored_mangas[:limit * 2] if len(scored_mangas) > limit else scored_mangas
    random.shuffle(top_candidates)
    selected = top_candidates[:limit]
    
    related = []
    for score, slug, manga in selected:
        related.append({
            "title": manga.get("title", ""),
            "slug": slug,
            "cover": manga.get("cover", ""),
            "latest_chapter": manga.get("chapters", [{}])[0].get("number", "1") if manga.get("chapters") else "1",
            "genres": manga.get("genres", [])[:3]  # Montrer jusqu'à 3 genres
        })
            
    return related
