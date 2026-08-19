from __future__ import annotations

import json
import logging
import sqlite3
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL_SECONDS, MANGA_TTL_SECONDS, cache
import os
from services.phenix_scans import get_phenix_api

_CACHE_DB = Path(__file__).resolve().parent.parent / "cache.db"


def _cache_get(key: str) -> dict | None:
    """Lecture directe du cache SQLite sans déclencher de scrape."""
    try:
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as conn:
            row = conn.execute("SELECT data FROM cache WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _cache_get_with_expiry(key: str) -> tuple[dict | None, float | None]:
    """Lecture directe du cache SQLite avec le timestamp d'expiration."""
    try:
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as conn:
            row = conn.execute("SELECT data, expires FROM cache WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0]), row[1]
    except Exception:
        pass
    return None, None

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    list_page_param: int = Query(default=1, ge=1, alias="list"),
) -> HTMLResponse:
    list_page = list_page_param
    error: str | None = None

    try:
        result = await cache.get_or_set(
            f"home:fr:latest:{list_page}",
            HOME_TTL_SECONDS,
            lambda: get_phenix_api().get_latest_mangas(page=list_page),
        )
        # get_latest_mangas retourne un tuple (mangas, total_pages)
        total_pages = 1
        if isinstance(result, (list, tuple)) and len(result) == 2:
            mangas, total_pages = result
        else:
            mangas = result if isinstance(result, list) else []
        popular = []
        popular_sidebar = []
    except Exception as exc:
        logger.warning("Unable to load homepage data page %s: %s", list_page, exc)
        mangas = []
        popular = []
        popular_sidebar = []
        total_pages = 1
        error = "😴 Noka n'arrive pas à joindre la bibliothèque… Réessaie dans un instant !"

    has_next_page = list_page < total_pages
    previous_page_url = f"/fr/?list={list_page - 1}" if list_page > 1 else None
    next_page_url = f"/fr/?list={list_page + 1}" if has_next_page else None

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
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "is_home": True,
        },
    )


@router.api_route("/sitemap-index.xml", methods=["GET", "HEAD"])
def sitemap_index() -> Response:
    """Sitemap index principal qui référence tous les sous-sitemaps."""
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap>
        <loc>{base_url}/sitemap.xml</loc>
        <lastmod>{datetime.now(timezone.utc).strftime("%Y-%m-%d")}</lastmod>
    </sitemap>
    <sitemap>
        <loc>{base_url}/sitemap-chapters.xml</loc>
        <lastmod>{datetime.now(timezone.utc).strftime("%Y-%m-%d")}</lastmod>
    </sitemap>
</sitemapindex>"""
    
    return Response(content=xml_content, media_type="application/xml")


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
def sitemap() -> Response:
    """Génère un sitemap XML dynamique basé sur les mangas actuellement en cache."""
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Récupérer toutes les clés de manga du cache (ex: 'manga:fr:death-penalty')
    manga_keys = cache.get_keys_by_prefix("manga:")
    entries = []  # (clé complète, slug)
    for key in manga_keys:
        if ":" in key:
            entries.append((key, key.rsplit(":", 1)[1]))

    # Génération du contenu XML
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    
    # 1. URL de la page d'accueil (version française sous /fr)
    xml_lines.append("    <url>")
    xml_lines.append(f"        <loc>{base_url}/fr</loc>")
    xml_lines.append(f"        <lastmod>{today}</lastmod>")
    xml_lines.append("        <changefreq>daily</changefreq>")
    xml_lines.append("        <priority>1.0</priority>")
    xml_lines.append("    </url>")
    
    # 2. URLs de tous les mangas en cache
    for key, slug in entries:
        # Calculer la date de création du cache pour un lastmod précis
        _manga, expires = _cache_get_with_expiry(key)
        if expires:
            creation_time = expires - MANGA_TTL_SECONDS
            lastmod = datetime.fromtimestamp(creation_time, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            lastmod = today
        xml_lines.append("    <url>")
        xml_lines.append(f"        <loc>{base_url}/fr/manga/{slug}</loc>")
        xml_lines.append(f"        <lastmod>{lastmod}</lastmod>")
        xml_lines.append("        <changefreq>weekly</changefreq>")
        xml_lines.append("        <priority>0.8</priority>")
        xml_lines.append("    </url>")
        
    xml_lines.append("</urlset>")
    xml_content = "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml")



@router.api_route("/sitemap-chapters.xml", methods=["GET", "HEAD"])
def sitemap_chapters() -> Response:
    """Génère un sitemap dédié aux 500 derniers chapitres sortis pour une indexation rapide."""
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    
    # Récupérer les mangas depuis le cache
    manga_keys = cache.get_keys_by_prefix("manga:")
    
    # Collecter tous les chapitres avec leur timestamp
    chapters_with_time = []
    for key in manga_keys:
        if ":" not in key:
            continue
        slug = key.rsplit(":", 1)[1]
        manga, expires = _cache_get_with_expiry(key)
        if not isinstance(manga, dict):
            continue
        
        # Calculer le timestamp de création du cache
        creation_time = (expires - MANGA_TTL_SECONDS) if expires else 0
        
        # Récupérer les chapitres
        chapters = manga.get("chapters", [])
        for chapter in chapters[:20]:  # Top 20 chapters par manga
            ch_num = chapter.get("number")
            if ch_num:
                chapters_with_time.append({
                    "url": f"{base_url}/fr/read/{slug}/{ch_num}",
                    "time": creation_time,
                })
    
    # Trier par timestamp décroissant et garder les 500 plus récents
    chapters_with_time.sort(key=lambda x: x["time"], reverse=True)
    latest_chapters = chapters_with_time[:500]
    
    # Génération du XML
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    
    for item in latest_chapters:
        lastmod = datetime.fromtimestamp(item['time'], tz=timezone.utc).strftime("%Y-%m-%d") if item['time'] else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        xml_lines.append("    <url>")
        xml_lines.append(f"        <loc>{item['url']}</loc>")
        xml_lines.append(f"        <lastmod>{lastmod}</lastmod>")
        xml_lines.append("        <changefreq>monthly</changefreq>")
        xml_lines.append("        <priority>0.6</priority>")
        xml_lines.append("    </url>")
    
    xml_lines.append("</urlset>")
    xml_content = "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml")

@router.get("/rss.xml", include_in_schema=False)
async def rss_feed() -> Response:
    """Flux RSS des 30 derniers mangas mis en cache."""
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    # Récupérer les slugs depuis le cache (lecture directe, sans déclencher de scrape)
    manga_keys = cache.get_keys_by_prefix("manga:")
    rss_keys = [(k, k.rsplit(":", 1)[1]) for k in manga_keys if ":" in k][:30]

    rss_items: list[str] = []
    for key, slug in rss_keys:
        manga, expires = _cache_get_with_expiry(key)
        if not isinstance(manga, dict) or not manga.get("title"):
            continue

        manga_title = manga.get("title", "")
        title = saxutils.escape(manga_title)
        
        # Récupérer le numéro du dernier chapitre s'il existe
        chapters = manga.get("chapters", [])
        latest_ch_num = str(chapters[0].get("number", "1")) if chapters else "1"
        
        # 1. Générer un hashtag de titre spécifique (ex: #sololeveling)
        clean_title_tag = "".join(c for c in manga_title.lower() if c.isalnum())
        specific_hashtag = f"#{clean_title_tag}" if clean_title_tag else ""
        
        # 2. Nettoyer et limiter la description d'origine
        raw_desc = manga.get("description") or "Discover and read your favorite manga online for free."
        if len(raw_desc) > 180:
            raw_desc = raw_desc[:177] + "..."
            
        # 3. Créer une description SEO ultra-vendeuse et riche en hashtags pour Pinterest
        seo_desc = (
            f"Lire {manga_title} Chapitre {latest_ch_num} en ligne gratuitement. Profitez d'une expérience ultra-rapide, responsive et sans pub sur MangaNoka ! "
            f"Noka est votre guide interactif vers votre prochain manga préféré. "
            f"{raw_desc} "
            f"\n\n#manga #manhwa #webtoon #liremanga #anime #manganoka {specific_hashtag}"
        )
        desc = saxutils.escape(seo_desc)
        # Couverture : proxy local en .jpg pour compatibilité Pinterest/n8n
        cover = manga.get("cover", "")
        cover_url = ""
        if cover:
            try:
                from services.image_cache import get_cache_filename
                filename, _ = get_cache_filename(cover)
                # Forcer l'extension en .jpg pour assurer la compatibilité universelle sur les réseaux (Pinterest, n8n, etc.)
                filename_jpg = filename.rsplit(".", 1)[0] + ".jpg"
                cover_url = f"{base_url}/fr/img-cdn/{filename_jpg}"
            except Exception as exc:
                logger.warning("Failed to generate proxy cover URL for RSS: %s", exc)
                cover_url = cover

        enclosure = f'<enclosure url="{saxutils.escape(cover_url)}" type="image/jpeg" />' if cover_url else ""

        # 4. Calculer la date réelle de création de l'entrée dans le cache pour un pubDate stable
        if expires:
            creation_time = expires - MANGA_TTL_SECONDS
            pub_date_dt = datetime.fromtimestamp(creation_time, tz=timezone.utc)
            pub_date_rfc = pub_date_dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        else:
            pub_date_rfc = now_rfc822

        # Le GUID est désormais unique par chapitre pour forcer la publication automatique de Pinterest
        unique_guid = f"{base_url}/fr/manga/{slug}#ch-{latest_ch_num}"
        # Le lien de l'article pointe directement vers le lecteur de chapitre si disponible pour maximiser le taux de conversion
        chapter_link = f"{base_url}/fr/read/{slug}/{latest_ch_num}" if chapters else f"{base_url}/fr/manga/{slug}"

        rss_items.append(f"""        <item>
            <title>Lire {title} Chapitre {latest_ch_num} en ligne gratuit </title>
            <link>{chapter_link}</link>
            <description>{desc}</description>
            {enclosure}
            <guid isPermaLink="false">{unique_guid}</guid>
            <pubDate>{pub_date_rfc}</pubDate>
        </item>""")

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
    <channel>
        <title>MangaNoka - Dernières mises à jour manga</title>
        <link>{base_url}/fr</link>
        <description>Lecture de manga rapide, responsive et sans publicité.</description>
        <language>fr-fr</language>
        <lastBuildDate>{now_rfc822}</lastBuildDate>
        <atom:link href="{base_url}/rss.xml" rel="self" type="application/rss+xml" />
{chr(10).join(rss_items)}
    </channel>
</rss>"""

    return Response(content=xml_content, media_type="application/xml; charset=utf-8")


@router.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    base_url = os.getenv("BASE_URL", "https://www.manganoka.xyz").rstrip("/")

    robots = f"""# Global
User-agent: *
Allow: /

Disallow: /api/
Disallow: /admin/
Disallow: /login/
Disallow: /register/
Disallow: /settings/
Disallow: /search/

# AI Crawlers
User-agent: GPTBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Bytespider
Allow: /

User-agent: Applebot
Allow: /

Sitemap: {base_url}/sitemap-index.xml
"""

    return Response(
        content=robots,
        media_type="text/plain; charset=utf-8",
    )

@router.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    """Sert le fichier du Service Worker pour la PWA à la racine du domaine."""
    sw_path = Path(__file__).resolve().parent.parent / "static" / "sw.js"
    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content=content, media_type="application/javascript")
    except Exception as exc:
        logger.warning("Failed to serve sw.js: %s", exc)
        return Response(content="", status_code=404)

@router.get("/{key_file}.txt", include_in_schema=False)
def indexnow_key(key_file: str) -> Response:
    """Sert dynamiquement le fichier texte de vérification requis par IndexNow (Bing/Yandex)."""
    indexnow_key = os.environ.get("INDEXNOW_KEY", "7a8e8b2fcd104ef9ac332a018af03324")
    if key_file == indexnow_key:
        return Response(content=indexnow_key, media_type="text/plain; charset=utf-8")
    return Response(content="Not Found", status_code=404, media_type="text/plain; charset=utf-8")

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

@router.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy(request: Request) -> HTMLResponse:
    """Sert la page Privacy Policy."""
    return templates.TemplateResponse(request, "privacy.html", {"request": request})

@router.get("/terms-conditions", response_class=HTMLResponse)
def terms_conditions(request: Request) -> HTMLResponse:
    """Sert la page Terms & Conditions."""
    return templates.TemplateResponse(request, "terms.html", {"request": request})


@router.get("/manga-list/{list_type}", include_in_schema=False)
async def list_mangas_page(list_type: str) -> RedirectResponse:
    """Anciennes listes MangaBats (supprimées) — redirection vers l'accueil Phenix Scans."""
    return RedirectResponse("/", status_code=301)


@router.get("/genre/{genre_slug}", include_in_schema=False)
async def genre_mangas_page(genre_slug: str) -> RedirectResponse:
    """Anciens filtres par genre MangaBats (supprimés) — redirection vers l'accueil Phenix Scans."""
    return RedirectResponse("/", status_code=301)
