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
BASE_URL = os.environ.get("BASE_URL", "https://manganoka.xyz")

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

@router.get("/img/{filename}")
def serve_cached_image(filename: str):
    """Redirection vers l'image en cache CDN."""
    return RedirectResponse(f"{BASE_URL}/img/cache_manga/{filename}")


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
    
    except InvalidDomainError as exc:
        logger.warning("Domaine non autorisé: %s", url)
        raise HTTPException(status_code=400, detail="Unauthorized URL") from exc
    
    except ImageTooBigError as exc:
        logger.warning("Image trop grande: %s", url)
        raise HTTPException(status_code=413, detail="Image too large (max 20 MB)") from exc
    
    except InvalidContentTypeError as exc:
        logger.warning("Content-Type invalide: %s", url)
        raise HTTPException(status_code=400, detail="File type not allowed") from exc
    
    except ImageCacheError as exc:
        logger.warning("Erreur cache image pour %s: %s", url, exc)
        raise HTTPException(status_code=502, detail="Source image unavailable") from exc
    
    except Exception as exc:
        logger.error("Erreur inattendue pour %s: %s", url, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error") from exc


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
    filename, _ = get_cache_filename(target_url)

    return RedirectResponse(f"{BASE_URL}/img/cache_manga/{filename}")


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
    filename, _ = get_cache_filename(target_url)
 
    return RedirectResponse(f"{BASE_URL}/img/cache_manga/{filename}")
