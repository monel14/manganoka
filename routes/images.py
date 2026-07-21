import os
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
import requests
import boto3

from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, Response

# Charger le .env local dès que le module est importé
dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path)

router = APIRouter()

# ==============================
# Variables N0C Storage & Config
# ==============================
BUCKET_NAME = os.environ.get("N0C_BUCKET")
ENDPOINT_URL = os.environ.get("N0C_ENDPOINT")
ACCESS_KEY = os.environ.get("N0C_ACCESS_KEY")
SECRET_KEY = os.environ.get("N0C_SECRET_KEY")
BASE_URL = os.environ.get("BASE_URL", "https://manganoka.xyz")

# Cache local de secours
LOCAL_CACHE_DIR = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "img_cache"
)

# ==============================
# Client S3 N0C
# ==============================
s3_client = boto3.client(
    "s3",
    endpoint_url=ENDPOINT_URL,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "path"
        },
        connect_timeout=3,
        retries={
            "max_attempts": 1
        }
    )
)

# ==============================
# Fonctions Utilitaires
# ==============================
def get_cache_filename(url: str) -> tuple[str, str]:
    """
    Extrait de manière robuste l'extension et génère un hash SHA256 pour le nom de fichier.
    Retourne un tuple : (nom_de_fichier, extension).
    """
    path = urlsplit(url).path
    ext = Path(path).suffix.lower().lstrip(".")
    
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
        
    filename = hashlib.sha256(url.encode()).hexdigest() + f".{ext}"
    return filename, ext


# ==============================
# Routes
# ==============================

@router.get("/img/{filename}")
def serve_cached_image(filename: str):
    return RedirectResponse(
        f"{BASE_URL}/img/cache_manga/{filename}"
    )


# ==============================
# Proxy image
# ==============================
ALLOWED_DOMAINS = {
    "demonicscans.org",
    "cdn.demoniclibs.com"
}

@router.get("/img-proxy")
def image_proxy(url: str):
    # Validation robuste de l'URL pour éviter les vulnérabilités SSRF / Open Redirect
    try:
        parsed_url = urlsplit(url)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Format d'URL invalide"
        ) from exc

    if parsed_url.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail="Protocole non supporté"
        )

    # Extraction et validation du domaine exact
    domain = parsed_url.hostname
    if not domain or domain not in ALLOWED_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail="URL non autorisée"
        )

    filename, ext = get_cache_filename(url)
    object_key = f"public/cache_manga/{filename}"
    local_file_path = LOCAL_CACHE_DIR / filename

    # ==========================
    # Tentative d'accès au cache S3
    # ==========================
    try:
        s3_client.head_object(
            Bucket=BUCKET_NAME,
            Key=object_key
        )
        # L'image existe dans S3 -> Redirection directe
        return RedirectResponse(f"{BASE_URL}/img/cache_manga/{filename}")

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "404":
            # L'image est absente de S3, on doit la télécharger depuis la source
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://demonicscans.org/"
                }
                r = requests.get(
                    url,
                    headers=headers,
                    timeout=15
                )
                r.raise_for_status()
                image_data = r.content
                content_type = r.headers.get("Content-Type", f"image/{ext}")
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Image source indisponible"
                ) from exc

            # Tentative de sauvegarde dans le S3
            try:
                s3_client.put_object(
                    Bucket=BUCKET_NAME,
                    Key=object_key,
                    Body=image_data,
                    ContentLength=len(image_data),
                    ContentType=content_type,
                    ACL="public-read"
                )
                return Response(
                    content=image_data,
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=31536000"
                    }
                )
            except Exception:
                # Si l'upload S3 échoue (ex: problème réseau/crédentiels), on se replie sur le cache local
                LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    with open(local_file_path, "wb") as f:
                        f.write(image_data)
                except Exception:
                    pass  # Si l'écriture disque échoue aussi, on retourne quand même l'image depuis la RAM
                
                return Response(
                    content=image_data,
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=31536000"
                    }
                )
        else:
            # Autre erreur S3 (ex: 403 Forbidden) -> repli sur le cache local
            pass

    except (BotoCoreError, Exception):
        # Timeout, erreur de connexion ou autre exception S3 -> repli sur le cache local
        pass

    # ==========================
    # Mode de secours : Cache Local
    # ==========================
    LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if local_file_path.exists():
        return FileResponse(
            local_file_path,
            media_type=f"image/{ext}"
        )

    # Si l'image n'est pas dans le cache local, on la télécharge
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://demonicscans.org/"
    }
    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=15
        )
        r.raise_for_status()
        image_data = r.content
        content_type = r.headers.get("Content-Type", f"image/{ext}")

        with open(local_file_path, "wb") as f:
            f.write(image_data)
            
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Source indisponible en mode local"
        ) from exc

    return FileResponse(
        local_file_path,
        media_type=content_type
    )


# ==============================
# Route sémantique d'image de chapitre
# Format: /{slug}/{chapter}/{page}.webp
# ==============================
@router.get("/{slug}/{chapter_num}/{page_num}.webp")
def chapter_image_semantic(slug: str, chapter_num: str, page_num: int):
    """
    Sert les images de chapitre avec des URLs sémantiques.
    Exemple: https://manganoka.xyz/i-became-the-rogue-first-prince/45/2.webp
    """
    try:
        from routes.reader import get_chapter_page
        page = get_chapter_page(slug, chapter_num)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Chapitre introuvable"
        )

    images = page.get("images", [])

    if page_num < 1 or page_num > len(images):
        raise HTTPException(
            status_code=404,
            detail="Page inexistante"
        )

    target_url = images[page_num - 1]
    filename, _ = get_cache_filename(target_url)

    return RedirectResponse(
        f"{BASE_URL}/img/cache_manga/{filename}"
    )


# ==============================
# Route alternative (backward compatibility)
# ==============================
@router.get("/chapter-img/{slug}/{chapter}/{page_num}.webp")
def chapter_image(slug: str, chapter: str, page_num: int):
    try:
        from routes.reader import get_chapter_page
        page = get_chapter_page(slug, chapter)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Chapitre introuvable"
        )

    images = page.get("images", [])

    if page_num < 1 or page_num > len(images):
        raise HTTPException(
            status_code=404,
            detail="Page inexistante"
        )

    target_url = images[page_num - 1]
    filename, _ = get_cache_filename(target_url)

    return RedirectResponse(
        f"{BASE_URL}/img/cache_manga/{filename}"
    )
