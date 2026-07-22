"""
Service de cache d'images avec support S3 et cache local.
Gère la sécurité, les limites de taille, et le streaming.
"""
from __future__ import annotations

import hashlib
import logging
import os
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ==============================
# Configuration
# ==============================
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024  # 20 Mo
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}
ALLOWED_DOMAINS = {
    "demonicscans.org",
    "cdn.demoniclibs.com",
}


class ImageCacheError(Exception):
    """Exception levée lors d'erreurs de cache d'image."""
    pass


class ImageTooBigError(ImageCacheError):
    """L'image dépasse la taille maximale autorisée."""
    pass


class InvalidContentTypeError(ImageCacheError):
    """Le Content-Type de l'image n'est pas autorisé."""
    pass


class InvalidDomainError(ImageCacheError):
    """Le domaine source n'est pas dans la whitelist."""
    pass


def get_cache_filename(url: str) -> tuple[str, str]:
    """
    Génère un nom de fichier de cache basé sur le hash SHA256 de l'URL.
    
    Returns:
        tuple[filename, extension]
    """
    path = urlsplit(url).path
    ext = Path(path).suffix.lower().lstrip(".")
    
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    
    filename = hashlib.sha256(url.encode()).hexdigest() + f".{ext}"
    return filename, ext


def validate_url(url: str) -> None:
    """
    Valide qu'une URL est sûre (SSRF protection).
    
    Raises:
        ImageCacheError: Si l'URL n'est pas valide
    """
    try:
        parsed = urlsplit(url)
    except Exception as exc:
        raise ImageCacheError("Format d'URL invalide") from exc
    
    if parsed.scheme not in {"http", "https"}:
        raise ImageCacheError("Protocole non supporté")
    
    domain = parsed.hostname
    if not domain or domain not in ALLOWED_DOMAINS:
        raise InvalidDomainError(f"Domaine non autorisé: {domain}")


class ImageCacheService:
    """Service de cache d'images avec S3 et fallback local."""
    
    def __init__(
        self,
        bucket_name: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        base_url: str,
        local_cache_dir: Path,
    ):
        self.bucket_name = bucket_name
        self.base_url = base_url
        self.local_cache_dir = Path(local_cache_dir)
        
        # Configuration du client S3
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                retries={"max_attempts": 1},
            ),
        )
    
    def get_s3_object_key(self, filename: str) -> str:
        """Génère la clé S3 pour un fichier."""
        return f"public/cache_manga/{filename}"
    
    def get_cdn_url(self, filename: str) -> str:
        """Génère l'URL CDN publique pour un fichier."""
        return f"{self.base_url}/img/cache_manga/{filename}"
    
    async def download_image_streaming(
        self,
        url: str,
        max_size: int = MAX_IMAGE_SIZE_BYTES,
    ) -> tuple[bytes, str]:
        """
        Télécharge une image avec streaming et validation.
        
        Args:
            url: URL source de l'image
            max_size: Taille maximale en octets
        
        Returns:
            tuple[image_data, content_type]
        
        Raises:
            ImageTooBigError: Si l'image est trop grande
            InvalidContentTypeError: Si le Content-Type n'est pas valide
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://demonicscans.org/",
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                
                # Validation du Content-Type
                content_type = response.headers.get("Content-Type", "")
                if not any(ct in content_type for ct in ALLOWED_CONTENT_TYPES):
                    raise InvalidContentTypeError(
                        f"Content-Type non autorisé: {content_type}"
                    )
                
                # Vérification de la taille déclarée
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_size:
                    raise ImageTooBigError(
                        f"Image trop grande: {content_length} > {max_size}"
                    )
                
                # Téléchargement avec streaming et limite de taille
                chunks = []
                total_size = 0
                
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise ImageTooBigError(
                            f"Image trop grande: {total_size} > {max_size}"
                        )
                    chunks.append(chunk)
                
                image_data = b"".join(chunks)
                
                # Normalisation du Content-Type
                if "jpeg" in content_type or "jpg" in content_type:
                    content_type = "image/jpeg"
                elif "png" in content_type:
                    content_type = "image/png"
                elif "webp" in content_type:
                    content_type = "image/webp"
                elif "gif" in content_type:
                    content_type = "image/gif"
                else:
                    content_type = "image/jpeg"  # fallback
                
                return image_data, content_type
    
    def upload_to_s3(
        self,
        object_key: str,
        image_data: bytes,
        content_type: str,
    ) -> bool:
        """
        Upload une image vers S3.
        
        Returns:
            True si succès, False sinon
        """
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=image_data,
                ContentLength=len(image_data),
                ContentType=content_type,
                # Enlevé ACL="public-read" car N0C S3 gère la visibilité par dossier (public/ vs private/)
                # et leverait une erreur 403 Forbidden Access Denied si on l'envoie.
            )
            logger.info("Image uploadée vers S3: %s", object_key)
            return True
        except Exception as exc:
            logger.warning("Échec upload S3 pour %s: %s", object_key, exc)
            return False
    
    def get_from_s3(self, object_key: str) -> bytes | None:
        """
        Récupère une image depuis S3.
        
        Returns:
            Image data si trouvée, None sinon
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=object_key,
            )
            return response["Body"].read()
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404" or error_code == "NoSuchKey":
                return None
            logger.warning("Erreur S3 get_object: %s", e)
            return None
        except Exception as exc:
            logger.warning("Erreur lors de la récupération S3: %s", exc)
            return None
    
    def save_to_local_cache(self, filename: str, image_data: bytes) -> bool:
        """
        Sauvegarde une image dans le cache local.
        
        Returns:
            True si succès, False sinon
        """
        try:
            self.local_cache_dir.mkdir(parents=True, exist_ok=True)
            local_path = self.local_cache_dir / filename
            with open(local_path, "wb") as f:
                f.write(image_data)
            logger.info("Image sauvegardée localement: %s", filename)
            return True
        except Exception as exc:
            logger.warning("Échec sauvegarde locale pour %s: %s", filename, exc)
            return False
    
    def get_from_local_cache(self, filename: str) -> bytes | None:
        """
        Récupère une image depuis le cache local.
        
        Returns:
            Image data si trouvée, None sinon
        """
        try:
            local_path = self.local_cache_dir / filename
            if local_path.exists():
                with open(local_path, "rb") as f:
                    return f.read()
        except Exception as exc:
            logger.warning("Erreur lecture cache local pour %s: %s", filename, exc)
        return None
    
    async def get_or_cache_image(self, url: str, bypass_validation: bool = False) -> tuple[bytes, str, str]:
        """
        Récupère une image (depuis S3, cache local, ou source).
        
        Architecture optimisée :
        1. Tentative GET S3 (1 requête au lieu de HEAD + GET)
        2. Si 404 S3 : télécharger source + upload S3 + save local
        3. Si erreur S3 : fallback cache local
        
        Returns:
            tuple[image_data, content_type, source]
            source: "s3", "local", ou "download"
        
        Raises:
            ImageCacheError: Si impossible de récupérer l'image
        """
        # Validation de l'URL
        if bypass_validation:
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"}:
                    raise ImageCacheError("Protocole non supporté")
            except Exception as exc:
                raise ImageCacheError("Format d'URL invalide") from exc
        else:
            validate_url(url)
        
        filename, ext = get_cache_filename(url)
        object_key = self.get_s3_object_key(filename)
        
        # Tentative GET S3 direct (optimisé)
        s3_data = self.get_from_s3(object_key)
        if s3_data:
            return s3_data, f"image/{ext}", "s3"
        
        # Image absente de S3 : téléchargement depuis la source
        try:
            image_data, content_type = await self.download_image_streaming(url)
        except ImageCacheError:
            # Erreur de téléchargement, tentative cache local
            local_data = self.get_from_local_cache(filename)
            if local_data:
                return local_data, f"image/{ext}", "local"
            raise
        
        # Upload vers S3 (best effort)
        uploaded = self.upload_to_s3(object_key, image_data, content_type)
        
        # Sauvegarde locale (fallback si S3 échoue)
        if not uploaded:
            self.save_to_local_cache(filename, image_data)
        
        return image_data, content_type, "download"
