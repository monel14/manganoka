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
from models import MangaDetail
from services.indexnow import ping_indexnow
from services.google_indexing import ping_google_indexing
from services.phenix_scans import get_phenix_api

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/manga/{slug}", response_class=HTMLResponse)
async def manga_detail(request: Request, slug: str, background_tasks: BackgroundTasks) -> HTMLResponse:
    # Récupérer le slug brut non-décodé depuis la socket HTTP
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

        # SÉCURITÉ ACTIVE (Self-Healing) : Si l'entrée de cache est corrompue ou None, on la répare !
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

    # Récupérer des mangas similaires depuis le cache (Related Manga)
    related_mangas = _get_related_mangas(slug, limit=6)

    # Indexation + Webhook : Ne déclencher que pour les vrais utilisateurs (pas les bots)
    if not _is_bot_request(request):
        background_tasks.add_task(ping_indexnow, [f"/manga/{slug}"])
        background_tasks.add_task(ping_google_indexing, [f"/manga/{slug}"])
        # Webhook Make.com : déclenché ici dans la route, pas dans le loader de cache
        background_tasks.add_task(_trigger_webhook, manga, slug)
    else:
        logger.debug("Bot detected, skipping indexing ping for /manga/%s", slug)

    return templates.TemplateResponse(
        request,
        "manga.html",
        {"request": request, "manga": manga, "slug": slug, "related_mangas": related_mangas},
    )


async def _trigger_webhook(manga: MangaDetail, slug: str) -> None:
    """
    Déclenche le webhook Make.com depuis la route (pas depuis le loader de cache).
    Protégé par l'anti-doublon GUID → s'exécute à chaque visite mais n'envoie qu'une seule fois par chapitre.
    Gère aussi la détection de nouveaux mangas (2 pins: ch.1 + dernier chapitre).
    """
    try:
        from services.webhook_pusher import push_to_make_webhook, is_new_manga_detection
        chapters = manga.get("chapters", [])

        is_new = is_new_manga_detection(slug)

        if is_new:
            # NOUVEAU MANGA : publier le premier chapitre pour l'annoncer
            first_chapter = chapters[-1] if chapters else None
            if first_chapter:
                first_ch_num = str(first_chapter.get("number", "1"))
                logger.info("Nouveau manga détecté: %s. Publication ch.%s (premier).", slug, first_ch_num)
                await push_to_make_webhook(
                    manga_title=manga["title"],
                    slug=slug,
                    latest_ch_num=first_ch_num,
                    raw_desc=manga.get("description"),
                    cover=manga.get("cover"),
                    all_chapters=chapters,
                    is_new_manga=True,
                )

        # Toujours publier le dernier chapitre
        latest_ch_num = str(chapters[0].get("number", "1")) if chapters else "1"
        await push_to_make_webhook(
            manga_title=manga["title"],
            slug=slug,
            latest_ch_num=latest_ch_num,
            raw_desc=manga.get("description"),
            cover=manga.get("cover"),
            all_chapters=chapters,
            is_new_manga=False,
        )
    except Exception as exc:
        logger.warning("Failed to trigger Make Webhook for manga %s: %s", slug, exc)


async def _load_manga(slug: str) -> MangaDetail:
    """Charge la fiche manga depuis l'API Phenix Scans."""
    api = get_phenix_api()
    return await api.get_manga_detail(slug)


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
    current_manga = _cache_get(f"manga:fr:{current_slug}")
    if current_manga and isinstance(current_manga, dict):
        genres_list = current_manga.get("genres") or []
        current_genres = set(genres_list) if isinstance(genres_list, list) else set()
    else:
        current_genres = set()
    
    # Récupérer tous les slugs de mangas en cache
    manga_keys = cache.get_keys_by_prefix("manga:fr:")
    all_slugs = [k.split("manga:fr:", 1)[1] for k in manga_keys if "manga:fr:" in k and k.split("manga:fr:", 1)[1] != current_slug]
    
    # Scorer les mangas par genres partagés
    scored_mangas = []
    for slug in all_slugs:
        manga = _cache_get(f"manga:fr:{slug}")
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
                manga = _cache_get(f"manga:fr:{slug}")
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
