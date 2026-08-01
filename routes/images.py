import os
import logging
from pathlib import Path

from dotenv import load_dotenv

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from services.image_cache import (
    ImageCacheService,
    ImageCacheError,
    ImageTooBigError,
    InvalidContentTypeError,
    InvalidDomainError,
    get_cache_filename,
)

# Charger le .env local dès que le module est importé
dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)

router = APIRouter()
logger = logging.getLogger(__name__)

# ==============================
# Configuration
# ==============================
BUCKET_NAME = os.environ.get("N0C_BUCKET")
ENDPOINT_URL = os.environ.get("N0C_ENDPOINT")
ACCESS_KEY = os.environ.get("N0C_ACCESS_KEY")
SECRET_KEY = os.environ.get("N0C_SECRET_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://www.manganoka.xyz")

LOCAL_CACHE_DIR = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "img_cache"
)

# ==============================
# Service d'image cache (singleton)
# ==============================
_image_cache_service: ImageCacheService | None = None


def get_image_cache_service() -> ImageCacheService:
    """Retourne le service de cache d'images singleton."""
    global _image_cache_service
    if _image_cache_service is None:
        _image_cache_service = ImageCacheService(
            bucket_name=BUCKET_NAME,
            endpoint_url=ENDPOINT_URL,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            base_url=BASE_URL,
            local_cache_dir=LOCAL_CACHE_DIR,
        )
    return _image_cache_service


# ==============================
# Routes
# ==============================

@router.get("/img-cdn/{filename}")
async def serve_cached_image(filename: str):
    """Sert l'image en cache CDN de manière robuste et directe (sans redirection)."""
    service = get_image_cache_service()
    
    # 1. Tentative de récupération depuis le cache local
    image_data = service.get_from_local_cache(filename)
    if image_data:
        ext = filename.split(".")[-1] if "." in filename else "jpg"
        return Response(
            content=image_data,
            media_type=f"image/{ext}",
            headers={"Cache-Control": "public, max-age=31536000"},
        )
        
    # 2. Tentative depuis le stockage S3 (via nos clés API privées)
    object_key = service.get_s3_object_key(filename)
    image_data = service.get_from_s3(object_key)
    if image_data:
        ext = filename.split(".")[-1] if "." in filename else "jpg"
        return Response(
            content=image_data,
            media_type=f"image/{ext}",
            headers={"Cache-Control": "public, max-age=31536000"},
        )
        
    raise HTTPException(status_code=404, detail="Image not found")


# ==============================
# Proxy image sécurisé avec streaming
# ==============================
@router.get("/img-proxy")
async def image_proxy(url: str):
    """
    Proxy sécurisé pour les images avec :
    - Whitelist de domaines (SSRF protection)
    - Limite de taille (20 Mo)
    - Validation Content-Type
    - Streaming (pas de chargement complet en RAM)
    - Cache S3 + fallback local
    - Image par défaut automatique si le téléchargement source échoue (503, 404, etc.)
    """
    service = get_image_cache_service()
    
    try:
        image_data, content_type, source = await service.get_or_cache_image(url)
        
        logger.info(
            "Image servie: %s octets, type=%s, source=%s",
            len(image_data),
            content_type,
            source,
        )
        
        return Response(
            content=image_data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )
    
    except Exception as exc:
        logger.warning("Échec du chargement de l'image proxy pour %s: %s. Service de secours de l'image par défaut.", url, exc)
        # Charger l'image par défaut locale de secours (noka_lost.svg)
        fallback_path = Path(__file__).resolve().parent.parent / "static" / "noka_lost.svg"
        try:
            with open(fallback_path, "rb") as f:
                fallback_data = f.read()
            return Response(
                content=fallback_data,
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=604800"}, # Cache de 7 jours pour l'image de secours
            )
        except Exception as file_exc:
            logger.error("Échec critique de lecture de l'image de secours locale: %s", file_exc)
            raise HTTPException(status_code=502, detail="Source image unavailable") from exc


# ==============================
# Route sémantique d'image de chapitre
# Format: /{slug}/{chapter}/{page}.webp
# ==============================
@router.get("/{slug}/{chapter_num}/{page_num}.webp")
async def chapter_image_semantic(request: Request, slug: str, chapter_num: str, page_num: int):
    """
    Sert les images de chapitre avec des URLs sémantiques.
    Exemple: https://manganoka.xyz/i-became-the-rogue-first-prince/45/2.webp
    """
    # Récupérer le slug et le chapitre bruts non-décodés pour préserver le double-encodage requis par le site source
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        parts = raw_path.strip("/").split("/")
        if len(parts) >= 3:
            slug = parts[0]
            chapter_num = parts[1]

    try:
        from routes.reader import get_chapter_page
        page = await get_chapter_page(slug, chapter_num)
    except Exception as exc:
        logger.warning("Chapitre introuvable: %s/%s", slug, chapter_num)
        raise HTTPException(status_code=404, detail="Chapter not found") from exc

    images = page.get("images", [])

    if page_num < 1 or page_num > len(images):
        raise HTTPException(status_code=404, detail="Page does not exist")

    target_url = images[page_num - 1]
    
    # Récupération et service de l'image directement (sans redirection) via le cache service
    service = get_image_cache_service()
    try:
        image_data, content_type, source = await service.get_or_cache_image(target_url, bypass_validation=True)
        return Response(
            content=image_data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )
    except Exception as exc:
        logger.warning("Échec du chargement de l'image du chapitre %s/%s page %s: %s. Service de secours de l'image par défaut.", slug, chapter_num, page_num, exc)
        fallback_path = Path(__file__).resolve().parent.parent / "static" / "noka_lost.svg"
        try:
            with open(fallback_path, "rb") as f:
                fallback_data = f.read()
            return Response(
                content=fallback_data,
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=604800"},
            )
        except Exception as file_exc:
            logger.error("Échec critique de lecture de l'image de secours locale: %s", file_exc)
            raise HTTPException(status_code=502, detail="Source image unavailable") from exc


# ==============================
# Route alternative (backward compatibility)
# ==============================
@router.get("/chapter-img/{slug}/{chapter}/{page_num}.webp")
async def chapter_image(request: Request, slug: str, chapter: str, page_num: int):
    """Route de compatibilité pour les anciennes URLs."""
    # Récupérer le slug et le chapitre bruts non-décodés pour préserver le double-encodage requis par le site source
    raw_path_bytes = request.scope.get("raw_path")
    if raw_path_bytes:
        raw_path = raw_path_bytes.decode("utf-8")
        if "/chapter-img/" in raw_path:
            parts = raw_path.split("/chapter-img/", 1)[1].split("/")
            if len(parts) >= 2:
                slug = parts[0]
                chapter = parts[1]

    try:
        from routes.reader import get_chapter_page
        page = await get_chapter_page(slug, chapter)
    except Exception as exc:
        logger.warning("Chapitre introuvable: %s/%s", slug, chapter)
        raise HTTPException(status_code=404, detail="Chapter not found") from exc

    images = page.get("images", [])

    if page_num < 1 or page_num > len(images):
        raise HTTPException(status_code=404, detail="Page does not exist")

    target_url = images[page_num - 1]
    
    service = get_image_cache_service()
    try:
        image_data, content_type, source = await service.get_or_cache_image(target_url, bypass_validation=True)
        return Response(
            content=image_data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )
    except Exception as exc:
        logger.warning("Échec du chargement de l'image alternative du chapitre %s/%s page %s: %s. Service de secours de l'image par défaut.", slug, chapter, page_num, exc)
        fallback_path = Path(__file__).resolve().parent.parent / "static" / "noka_lost.svg"
        try:
            with open(fallback_path, "rb") as f:
                fallback_data = f.read()
            return Response(
                content=fallback_data,
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=604800"},
            )
        except Exception as file_exc:
            logger.error("Échec critique de lecture de l'image de secours locale: %s", file_exc)
            raise HTTPException(status_code=502, detail="Source image unavailable") from exc
