"""Configuration partagée MangaNoka.

Point unique pour construire l'URL de base de la version française :
elle se termine TOUJOURS par exactement un « /fr », que BASE_URL
(dans .env) le contienne ou non. Cela évite le double routage
(ex: https://manganoka.xyz/fr/fr/...).
"""
import os

DEFAULT_BASE_URL = "https://manganoka.xyz/fr"


def get_fr_base_url() -> str:
    """Retourne la base absolue de la version FR, terminée par un seul /fr."""
    raw = os.environ.get("BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw.endswith("/fr"):
        raw += "/fr"
    return raw
