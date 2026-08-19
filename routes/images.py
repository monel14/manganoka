import os
import logging
import hashlib
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
BASE_URL = os.environ.get("BASE_URL", "https://manganoka.xyz/fr")

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


def make_image_response(
    request: Request | None,
    image_data: bytes,
    content_type: str,
    max_age: int = 31536000,
    immutable: bool = True,
) -> Response:
    """
    Génère une réponse HTTP optimisée pour les images :
    - Hash ETag MD5 pour support HTTP 304 Not Modified
    - Cache-Control public, max-age, immutable
    - En-tête Vary pour compatibilité CDN / proxy
    """
    etag = f'"{hashlib.md5(image_data).hexdigest()}"'
    cache_control = f"public, max-age={max_age}"
    if immutable:
        cache_control += ", immutable"

    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "Vary": "Accept, Accept-Encoding",
    }

    if request:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and etag in if_none_match:
            return Response(status_code=304, headers=headers)

    return Response(
        content=image_data,
        media_type=content_type,
        headers=headers,
    )


# ==============================
# Routes
# ==============================

@router.get("/img-cdn/{filename}")
async def serve_cached_image(request: Request, filename: str):
    """Sert l'image en cache CDN de manière robuste et directe (sans redirection), avec conversion JPEG optionnelle."""
    service = get_image_cache_service()
    
    is_jpeg_requested = filename.lower().endswith((".jpg", ".jpeg"))
    base_hash = filename.rsplit(".", 1)[0]
    
    image_data = None
    source_ext = "webp"
    
    # Rechercher s'il y a un fichier cache valide avec n'importe quelle extension courante
    for ext in ["webp", "png", "jpg", "jpeg"]:
        cached_name = f"{base_hash}.{ext}"
        
        # 1. Tentative locale
        image_data = service.get_from_local_cache(cached_name)
        if image_data:
            source_ext = ext
            break
            
        # 2. Tentative S3
        object_key = service.get_s3_object_key(cached_name)
        image_data = service.get_from_s3(object_key)
        if image_data:
            source_ext = ext
            break
            
    if not image_data:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Si conversion en JPEG demandée pour compatibilité réseaux sociaux
    if is_jpeg_requested and source_ext not in {"jpg", "jpeg"}:
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(image_data))
            # Convertir en RGB (le JPEG ne supporte pas la transparence)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3] if len(img.split()) >= 4 else None)
                img = background
            else:
                img = img.convert("RGB")
                
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=85)
            image_data = output.getvalue()
            source_ext = "jpg"
        except Exception as exc:
            logger.warning("Échec de conversion d'image Pillow en JPEG pour %s: %s", filename, exc)
            
    return make_image_response(
        request=request,
        image_data=image_data,
        content_type="image/jpeg" if is_jpeg_requested else f"image/{source_ext}",
    )


# ==============================
# Proxy image sécurisé avec streaming
# ==============================
@router.get("/img-proxy")
async def image_proxy(request: Request, url: str):
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
        
        return make_image_response(
            request=request,
            image_data=image_data,
            content_type=content_type,
        )
    
    except Exception as exc:
        logger.warning("Échec du chargement de l'image proxy pour %s: %s", url, exc)
        raise HTTPException(status_code=404, detail="Image unavailable") from exc


# ==============================
# Route sémantique d'image de chapitre
# Format: /{slug}/{chapter}/{page}.webp
# ==============================
@router.get("/{slug}/{chapter_num}/{page_num}.webp")
async def chapter_image_semantic(request: Request, slug: str, chapter_num: str, page_num: int):
    """
    Sert les images de chapitre avec des URLs sémantiques.
    Exemple: https://www.manganoka.xyz/i-became-the-rogue-first-prince/45/2.webp
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
        return make_image_response(
            request=request,
            image_data=image_data,
            content_type=content_type,
        )
    except Exception as exc:
        logger.warning("Échec du chargement de l'image du chapitre %s/%s page %s: %s", slug, chapter_num, page_num, exc)
        raise HTTPException(status_code=404, detail="Image unavailable") from exc


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
        return make_image_response(
            request=request,
            image_data=image_data,
            content_type=content_type,
        )
    except Exception as exc:
        logger.warning("Échec du chargement de l'image alternative du chapitre %s/%s page %s: %s", slug, chapter, page_num, exc)
        raise HTTPException(status_code=404, detail="Image unavailable") from exc
