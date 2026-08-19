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
    """Crée les tables nécessaires si elles n'existent pas."""
    with sqlite3.connect(DB_PATH) as conn:
        # Table des GUIDs déjà envoyés au webhook
        conn.execute(
            "CREATE TABLE IF NOT EXISTS posted_pins "
            "(guid TEXT PRIMARY KEY, posted_at REAL)"
        )
        # Table pour tracker le quota quotidien
        conn.execute(
            "CREATE TABLE IF NOT EXISTS webhook_daily_quota "
            "(date TEXT PRIMARY KEY, count INTEGER DEFAULT 0)"
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


def check_daily_quota(max_per_day: int = 50) -> bool:
    """
    Vérifie si le quota quotidien de pins n'est pas dépassé.
    Par défaut: max 50 pins/jour (recommandation Pinterest).
    """
    from datetime import date
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            today = date.today().isoformat()
            row = conn.execute(
                "SELECT count FROM webhook_daily_quota WHERE date = ?",
                (today,)
            ).fetchone()
            
            current_count = row[0] if row else 0
            
            if current_count >= max_per_day:
                logger.warning(
                    "Webhook Pusher: Quota quotidien atteint (%d/%d). "
                    "Aucun nouveau pin ne sera publié aujourd'hui.",
                    current_count, max_per_day
                )
                return False
            
            return True
    except Exception as exc:
        logger.warning("Webhook Pusher: Erreur vérification quota: %s", exc)
        return True  # En cas d'erreur, on laisse passer


def increment_daily_quota():
    """Incrémente le compteur quotidien de pins envoyés."""
    from datetime import date
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            today = date.today().isoformat()
            conn.execute(
                "INSERT INTO webhook_daily_quota (date, count) VALUES (?, 1) "
                "ON CONFLICT(date) DO UPDATE SET count = count + 1",
                (today,)
            )
    except Exception as exc:
        logger.warning("Webhook Pusher: Erreur incrémentation quota: %s", exc)


def is_new_manga_detection(slug: str) -> bool:
    """
    Détecte si un manga est nouveau (première fois qu'il est chargé).
    Utilise une table dédiée pour tracker les mangas déjà vus.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Créer la table si elle n'existe pas
            conn.execute(
                "CREATE TABLE IF NOT EXISTS seen_mangas "
                "(slug TEXT PRIMARY KEY, first_seen_at REAL)"
            )
            
            # Vérifier si le manga existe
            row = conn.execute(
                "SELECT 1 FROM seen_mangas WHERE slug = ?",
                (slug,)
            ).fetchone()
            
            is_new = row is None
            
            # Si nouveau, l'enregistrer
            if is_new:
                conn.execute(
                    "INSERT INTO seen_mangas VALUES (?, ?)",
                    (slug, time.time())
                )
                conn.commit()
                logger.info("Webhook Pusher: Nouveau manga détecté: %s", slug)
            
            return is_new
    except Exception as exc:
        logger.warning("Webhook Pusher: Erreur détection nouveau manga: %s", exc)
        return False  # En cas d'erreur, considérer comme existant


async def push_to_make_webhook(manga_title: str, slug: str, latest_ch_num: str, raw_desc: str, cover: str, all_chapters: list = None, is_new_manga: bool = False) -> None:
    """
    Envoie en temps réel un payload JSON structuré au Webhook de Make.com.
    Utilise la table SQLite `posted_pins` pour garantir qu'un chapitre n'est envoyé qu'UNE SEULE FOIS.
    
    ANTI-SPAM: 
    - Ne publie QUE le dernier chapitre de chaque manga
    - OU le premier chapitre si c'est un nouveau manga (is_new_manga=True)
    
    Stratégie: 1 pin par manga = contenu frais et pertinent sans spam.
    """
    init_db()
    
    # ANTI-SPAM: Ne publier QUE le dernier chapitre (le plus récent)
    # EXCEPTION: Si c'est un nouveau manga, on publie le premier chapitre pour annoncer sa disponibilité
    if not is_new_manga and all_chapters:
        # Récupérer le numéro du dernier chapitre
        latest_chapter_num = str(all_chapters[0].get("number", "")) if all_chapters else ""
        if latest_ch_num != latest_chapter_num:
            logger.debug(
                "Webhook Pusher: Chapitre '%s' de '%s' n'est pas le dernier chapitre (%s). "
                "Publication skippée pour éviter le spam Pinterest. Seul le dernier chapitre est publié.",
                latest_ch_num, manga_title, latest_chapter_num
            )
            return
    
    base_url = os.environ.get("BASE_URL", "https://manganoka.xyz/fr").rstrip("/")
    guid = f"{base_url}/fr/manga/{slug}#ch-{latest_ch_num}"
    
    # 1. Éviter 100 % des doublons : si le chapitre est déjà publié, on s'arrête immédiatement !
    if is_guid_posted(guid):
        logger.info("Make Webhook Pusher: Le chapitre '%s ch %s' a déjà été envoyé. Envoi sauté.", manga_title, latest_ch_num)
        return
    
    # 2. Vérifier le quota quotidien (max 50 pins/jour par défaut)
    if not check_daily_quota(max_per_day=50):
        # Marquer quand même pour éviter de re-tenter demain
        mark_guid_posted(guid)
        return

    webhook_url = os.environ.get("MAKE_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Make Webhook Pusher: MAKE_WEBHOOK_URL manquant dans .env. Sauvegarde de l'état mais envoi sauté.")
        # On le marque quand même pour ne pas re-tenter si l'URL est configurée plus tard
        mark_guid_posted(guid)
        return

    # 2. Sélectionner l'image optimale pour Buffer/Pinterest
    # - Buffer ne supporte pas le WebP
    # - Pinterest rejette les URLs sans extension
    # - Solution: /img-cdn/{hash}.jpg qui convertit automatiquement en JPEG via Pillow
    # - On préchauffe le cache si l'image n'est pas encore présente
    image_url = f"{base_url}/static/og_image.png"  # Fallback par défaut
    if cover:
        try:
            from services.image_cache import get_cache_filename
            from routes.images import get_image_cache_service
            
            filename, _ = get_cache_filename(cover)
            filename_jpg = filename.rsplit(".", 1)[0] + ".jpg"
            
            service = get_image_cache_service()
            
            # Vérifier si l'image est déjà en cache (local ou S3)
            in_local = service.get_from_local_cache(filename) or service.get_from_local_cache(filename_jpg)
            in_s3 = False
            if not service.disable_s3:
                in_s3 = service.get_from_s3(service.get_s3_object_key(filename)) is not None
            
            if not in_local and not in_s3:
                # Préchauffer le cache en téléchargeant l'image maintenant
                logger.info("Webhook Pusher: Préchauffage du cache image pour %s", cover)
                image_data, content_type = await service.download_image_streaming(cover)
                service.save_to_local_cache(filename, image_data)
                if not service.disable_s3:
                    service.upload_to_s3(service.get_s3_object_key(filename), image_data, content_type)
            
            # URL finale avec extension .jpg → conversion JPEG automatique dans /img-cdn
            image_url = f"{base_url}/fr/img-cdn/{filename_jpg}"
        except Exception as exc:
            logger.warning("Webhook Pusher: Échec préchargement image, utilisation og_image: %s", exc)
            image_url = f"{base_url}/static/og_image.png"

    # 3. Générer le hashtag sémantique de titre spécifique (ex: #sololeveling)
    clean_title_tag = "".join(c for c in manga_title.lower() if c.isalnum())
    specific_hashtag = f"#{clean_title_tag}" if clean_title_tag else ""
    
    # 4. Limiter et formater la description pour Pinterest (max 500 chars)
    clean_desc_source = raw_desc or "Discover and read your favorite manga online for free."
    if len(clean_desc_source) > 180:
        clean_desc_source = clean_desc_source[:177] + "..."
    
    # Message différent pour nouveau manga vs nouveau chapitre
    if is_new_manga:
        seo_desc = (
            f"🆕 NEW MANGA: {manga_title} is now available! Start reading Chapter {latest_ch_num} online for free. "
            f"Enjoy a high-speed, mobile-responsive, and ad-free experience on MangaNoka! "
            f"{clean_desc_source} "
            f"\n\n#newmanga #manga #manhwa #webtoon #readmanga #anime #manganoka {specific_hashtag}"
        )
        pin_title = f"🆕 NEW: Read {manga_title} Online Free - Chapter {latest_ch_num} Available Now!"
    else:
        seo_desc = (
            f"Read {manga_title} Chapter {latest_ch_num} online for free. Enjoy a high-speed, mobile-responsive, and ad-free experience on MangaNoka! "
            f"{clean_desc_source} "
            f"\n\n#manga #manhwa #webtoon #readmanga #anime #manganoka {specific_hashtag}"
        )
        pin_title = f"Read {manga_title} Chapter {latest_ch_num} Online Free - No Ads & High-Speed"

    payload = {
        "title": pin_title,
        "link": f"{base_url}/fr/read/{slug}/{latest_ch_num}",
        "description": seo_desc,
        "image_url": image_url,
        "guid": guid
    }
    
    logger.info("Make Webhook Pusher: Envoi au webhook de '%s ch %s'... (nouveau manga: %s)", manga_title, latest_ch_num, is_new_manga)
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=12.0)
            if r.status_code in (200, 201, 202):
                logger.info("Make Webhook Pusher: Payload envoyé avec succès ! (HTTP %d)", r.status_code)
                mark_guid_posted(guid)
                increment_daily_quota()  # Incrémenter le compteur quotidien
            else:
                logger.warning("Make Webhook Pusher: Retour inattendu du Webhook (HTTP %d): %s", r.status_code, r.text)
        except Exception as exc:
            logger.warning("Make Webhook Pusher: Échec de connexion au Webhook de Make: %s", exc)
