import os
import logging
import urllib.parse
import sqlite3
import time
import httpx
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "cache.db"


def init_db():
    """Crée la table des GUIDs déjà envoyés au webhook si elle n'existe pas."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS posted_pins "
            "(guid TEXT PRIMARY KEY, posted_at REAL)"
        )


def is_guid_posted(guid: str) -> bool:
    """Vérifie si ce chapitre/manga a déjà été envoyé au webhook."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT 1 FROM posted_pins WHERE guid=?", (guid,)).fetchone()
            return row is not None
    except Exception as exc:
        logger.warning("Make Webhook Pusher: Échec de lecture de posted_pins: %s", exc)
        return False


def mark_guid_posted(guid: str):
    """Marque ce guid comme envoyé."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO posted_pins VALUES (?, ?)",
                (guid, time.time())
            )
    except Exception as exc:
        logger.warning("Make Webhook Pusher: Échec d'écriture dans posted_pins: %s", exc)


async def push_to_make_webhook(manga_title: str, slug: str, latest_ch_num: str, raw_desc: str, cover: str, bakacover: str | None = None) -> None:
    """
    Envoie en temps réel un payload JSON structuré au Webhook de Make.com.
    Utilise la table SQLite `posted_pins` pour garantir qu'un chapitre n'est envoyé qu'UNE SEULE FOIS.
    """
    init_db()
    
    base_url = os.environ.get("BASE_URL", "https://www.manganoka.xyz").rstrip("/")
    guid = f"{base_url}/manga/{slug}#ch-{latest_ch_num}"
    
    # 1. Éviter 100 % des doublons : si le chapitre est déjà publié, on s'arrête immédiatement !
    if is_guid_posted(guid):
        logger.info("Make Webhook Pusher: Le chapitre '%s ch %s' a déjà été envoyé. Envoi sauté.", manga_title, latest_ch_num)
        return

    webhook_url = os.environ.get("MAKE_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Make Webhook Pusher: MAKE_WEBHOOK_URL manquant dans .env. Sauvegarde de l'état mais envoi sauté.")
        # On le marque quand même pour ne pas re-tenter si l'URL est configurée plus tard
        mark_guid_posted(guid)
        return

    # 2. Sélectionner l'enclot d'image JPEG optimal (MangaBaka direct ou notre proxy local)
    if bakacover:
        image_url = bakacover
    elif cover:
        try:
            from services.image_cache import get_cache_filename
            filename, _ = get_cache_filename(cover)
            filename_jpg = filename.rsplit(".", 1)[0] + ".jpg"
            image_url = f"{base_url}/img-cdn/{filename_jpg}"
        except Exception:
            image_url = cover
    else:
        image_url = f"{base_url}/static/noka_lost.svg"

    # 3. Générer le hashtag sémantique de titre spécifique (ex: #sololeveling)
    clean_title_tag = "".join(c for c in manga_title.lower() if c.isalnum())
    specific_hashtag = f"#{clean_title_tag}" if clean_title_tag else ""
    
    # 4. Limiter et formater la description pour Pinterest (max 500 chars)
    clean_desc_source = raw_desc or "Discover and read your favorite manga online for free."
    if len(clean_desc_source) > 180:
        clean_desc_source = clean_desc_source[:177] + "..."
        
    seo_desc = (
        f"Read {manga_title} Chapter {latest_ch_num} online for free. Enjoy a high-speed, mobile-responsive, and ad-free experience on MangaNoka! "
        f"{clean_desc_source} "
        f"\n\n#manga #manhwa #webtoon #readmanga #anime #manganoka {specific_hashtag}"
    )

    payload = {
        "title": f"Read {manga_title} Chapter {latest_ch_num} Online Free - No Ads & High-Speed",
        "link": f"{base_url}/read/{slug}/{latest_ch_num}",
        "description": seo_desc,
        "image_url": image_url,
        "guid": guid
    }
    
    logger.info("Make Webhook Pusher: Envoi au webhook de '%s ch %s'...", manga_title, latest_ch_num)
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=12.0)
            if r.status_code in (200, 201, 202):
                logger.info("Make Webhook Pusher: Payload envoyé avec succès ! (HTTP %d)", r.status_code)
                mark_guid_posted(guid)
            else:
                logger.warning("Make Webhook Pusher: Retour inattendu du Webhook (HTTP %d): %s", r.status_code, r.text)
        except Exception as exc:
            logger.warning("Make Webhook Pusher: Échec de connexion au Webhook de Make: %s", exc)
