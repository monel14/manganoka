from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)

BASE_API_URL = "https://api.phenix-scans.co/api"
BASE_UPLOADS_URL = "https://api.phenix-scans.co"
TIMEOUT = 30.0

# --- Réglages anti rate-limit (limite API : 800 req / 60 s) ---
MIN_INTERVAL = 0.25        # secondes minimales entre 2 requêtes (pacing global)
LOW_LIMIT = 30             # si x-ratelimit-remaining descend sous ce seuil, on pause
MAX_RETRIES = 4            # tentatives max par requête
BACKOFF_BASE = 0.5         # base du backoff exponentiel (x2 à chaque essai)


class ChapterDict(TypedDict):
    number: str
    title: str
    url: str
    date: str


class MangaDict(TypedDict):
    title: str
    slug: str
    cover: str
    type: str
    synopsis: str
    description: str
    author: str
    status: str
    rating: float
    genres: list[str]
    chapters: list[ChapterDict]
    chapter: ChapterDict


class PhenixScansAPI:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=TIMEOUT)
        self._pace_lock = asyncio.Lock()
        self._next_slot = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ #
    #  Garde-fou rate-limit : pacing + retry + lecture des en-têtes        #
    # ------------------------------------------------------------------ #

    async def _pace(self) -> None:
        """Garantit un intervalle minimal entre deux requêtes (lissage des bursts)."""
        async with self._pace_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + MIN_INTERVAL

    async def _request(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET avec gestion complète du rate limiting : 429 + Retry-After,
        backoff exponentiel sur 5xx/erreurs réseau, pause préventive
        quand le quota restant devient faible."""
        for attempt in range(MAX_RETRIES):
            await self._pace()
            try:
                resp = await self.client.get(url, params=params)
            except httpx.HTTPError:
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                continue

            # Quota restant annoncé par l'API -> pause préventive si bas
            remaining = resp.headers.get("x-ratelimit-remaining")
            if remaining is not None:
                try:
                    if int(remaining) < LOW_LIMIT:
                        reset = int(resp.headers.get("x-ratelimit-reset", "60"))
                        logger.info(
                            "Rate limit presque atteint (%s restants) — pause %.0fs",
                            remaining, reset + 1,
                        )
                        await asyncio.sleep(reset + 1)
                except ValueError:
                    pass

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after else BACKOFF_BASE * (2 ** attempt)
                logger.warning("HTTP 429 sur %s — nouvel essai dans %.1fs", url, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                continue

            resp.raise_for_status()
            return resp

        raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives : {url}")

    # ------------------------------------------------------------------ #
    #  Page d'accueil                                                      #
    # ------------------------------------------------------------------ #

    async def get_latest_mangas(
        self, page: int = 1, limit: int = 12
    ) -> tuple[list[MangaDict], int]:
        """Retourne les derniers mangas mis à jour avec leurs 3 derniers chapitres."""
        try:
            response = await self._request(
                f"{BASE_API_URL}/manga",
                params={"page": page, "limit": limit},
            )
            all_data = response.json().get("data", [])

            # ⚠️ L'API renvoie TOUS les mangas quel que soit `limit` et `page`
            # (743 items, même liste à chaque page). On découpe donc côté client,
            # AVANT de lancer les requêtes chapitres, sinon chaque visite
            # d'accueil = 700+ requêtes -> rate limit immédiat (quota 800/min).
            start = (page - 1) * limit
            data = all_data[start : start + limit]
            total_pages = max(1, -(-len(all_data) // limit))  # ceil

            # Semaphore à 2 pour lisser les requêtes parallèles et éviter les 429
            sem = asyncio.Semaphore(2)

            async def fetch_chapters_for_manga(item: dict) -> list[ChapterDict]:
                async with sem:
                    manga_id = item.get("id", "")
                    slug = item.get("slug", "")
                    chapters: list[ChapterDict] = []
                    if not manga_id:
                        return chapters

                    try:
                        ch_resp = await self._request(
                            f"{BASE_API_URL}/manga/{manga_id}/chapters"
                        )
                        chapters_data = ch_resp.json().get("data", [])

                        # Tri décroissant par numéro de chapitre
                        chapters_data.sort(
                            key=lambda x: float(x.get("number", 0) or 0),
                            reverse=True,
                        )

                        for ch in chapters_data[:3]:  # Top 3 derniers chapitres
                            ch_num = str(ch.get("number", "1")).rstrip(".0") or "1"
                            chapters.append(
                                {
                                    "number": ch_num,
                                    "title": f"Chapitre {ch_num}",
                                    "url": f"/fr/read/{slug}/{ch_num}",
                                    "date": self._format_date(
                                        ch.get("createdAt", "")
                                    ),
                                }
                            )
                    except Exception:
                        logger.warning(
                            "Failed to fetch homepage chapters for manga ID %s",
                            manga_id,
                        )
                    return chapters

            tasks = [fetch_chapters_for_manga(item) for item in data]
            chapters_lists = await asyncio.gather(*tasks, return_exceptions=True)

            mangas: list[MangaDict] = []
            for idx, item in enumerate(data):
                chapters = (
                    chapters_lists[idx]
                    if not isinstance(chapters_lists[idx], Exception)
                    else []
                )
                mangas.append(
                    {
                        "title": item.get("title", ""),
                        "slug": item.get("slug", ""),
                        "cover": f"{BASE_UPLOADS_URL}/{item.get('coverImage', '')}",
                        "type": item.get("type", "Manhwa"),
                        "synopsis": item.get("synopsis", ""),
                        "description": item.get("synopsis", ""),
                        "author": "Inconnu",
                        "status": item.get("status", "Ongoing"),
                        "rating": float(item.get("averageRating") or 0.0),
                        "genres": [g["name"] for g in item.get("genres", [])],
                        "chapters": chapters,
                        "chapter": chapters[0]
                        if chapters
                        else {"number": "", "title": "", "url": "", "date": ""},
                    }
                )
            return mangas, total_pages

        except Exception as exc:
            logger.error("Error fetching latest mangas: %s", exc)
            return [], 1

    # ------------------------------------------------------------------ #
    #  Recherche (locale, sur le catalogue complet)                        #
    # ------------------------------------------------------------------ #

    async def search_mangas(self, query: str, limit: int = 30) -> list[dict]:
        """Recherche un manga par titre dans le catalogue Phenix Scans.

        L'API n'expose pas d'endpoint de recherche : on récupère le
        catalogue complet (1 seule requête — l'API renvoie tout quel que
        soit `limit`) et on filtre côté client, sans accent.
        Retourne des dicts légers : title, slug, cover, views.
        """
        try:
            response = await self._request(
                f"{BASE_API_URL}/manga", params={"page": 1, "limit": 1}
            )
            all_data = response.json().get("data", [])

            # Normalisation : minuscules + suppression des accents
            import unicodedata

            def _norm(s: str) -> str:
                s = unicodedata.normalize("NFD", str(s or ""))
                s = "".join(c for c in s if unicodedata.category(c) != "Mn")
                return s.lower()

            q_norm = _norm(query)
            results: list[dict] = []
            for item in all_data:
                title = item.get("title", "")
                if q_norm in _norm(title):
                    results.append(
                        {
                            "title": title,
                            "slug": item.get("slug", ""),
                            "cover": f"{BASE_UPLOADS_URL}/{item.get('coverImage', '')}",
                            "views": str(item.get("views", "") or ""),
                        }
                    )
                    if len(results) >= limit:
                        break
            return results
        except Exception as exc:
            logger.error("Error searching mangas for '%s': %s", query, exc)
            return []

    # ------------------------------------------------------------------ #
    #  Fiche manga                                                         #
    # ------------------------------------------------------------------ #

    async def get_manga_detail(self, slug: str) -> MangaDict | None:
        """Retourne la fiche complète d'un manga avec tous ses chapitres."""
        try:
            response = await self._request(f"{BASE_API_URL}/manga/{slug}")
            data = response.json().get("data", {})
            manga_id = data.get("id", "")

            chapters: list[ChapterDict] = []
            if manga_id:
                try:
                    ch_resp = await self._request(
                        f"{BASE_API_URL}/manga/{manga_id}/chapters"
                    )
                    chapters_data = ch_resp.json().get("data", [])

                    for ch in chapters_data:
                        ch_num = str(ch.get("number", "1")).rstrip(".0") or "1"
                        chapters.append(
                            {
                                "number": ch_num,
                                "title": f"Chapitre {ch_num}",
                                "url": f"/fr/read/{slug}/{ch_num}",
                                "date": self._format_date(ch.get("createdAt", "")),
                            }
                        )
                    chapters.sort(
                        key=lambda x: float(x["number"] or 0), reverse=True
                    )
                except Exception as ch_exc:
                    logger.error(
                        "Failed to fetch chapters for manga ID %s: %s", manga_id, ch_exc
                    )

            return {
                "title": data.get("title", ""),
                "slug": data.get("slug", slug),
                "cover": f"{BASE_UPLOADS_URL}/{data.get('coverImage', '')}",
                "type": data.get("type", "Manhwa"),
                "synopsis": data.get("synopsis", ""),
                "description": data.get("synopsis", ""),
                "author": "Inconnu",
                "status": data.get("status", "Ongoing"),
                "rating": float(data.get("averageRating") or 0.0),
                "genres": [g["name"] for g in data.get("genres", [])],
                "chapters": chapters,
                "chapter": chapters[0]
                if chapters
                else {"number": "", "title": "", "url": "", "date": ""},
            }

        except Exception as exc:
            logger.error("Error fetching manga %s: %s", slug, exc)
            return None

    # ------------------------------------------------------------------ #
    #  Images d'un chapitre                                                #
    # ------------------------------------------------------------------ #

    async def get_chapter_images(self, slug: str, chapter: str) -> list[str]:
        """Retourne la liste ordonnée des URLs d'images d'un chapitre."""
        try:
            # 1. Récupérer le manga_id
            manga_resp = await self._request(f"{BASE_API_URL}/manga/{slug}")
            manga_id = manga_resp.json().get("data", {}).get("id")
            if not manga_id:
                return []

            # 2. Liste des chapitres
            ch_list_resp = await self._request(
                f"{BASE_API_URL}/manga/{manga_id}/chapters"
            )
            chapters_data = ch_list_resp.json().get("data", [])

            # 3. Trouver le chapter_id correspondant au numéro demandé
            chapter_id = None
            for ch in chapters_data:
                try:
                    if float(ch.get("number", -1)) == float(chapter):
                        chapter_id = ch.get("id")
                        break
                except (ValueError, TypeError):
                    continue

            if not chapter_id:
                logger.warning(
                    "Chapter %s not found in chapters list for manga %s", chapter, slug
                )
                return []

            # 4. Requête des images du chapitre
            images_resp = await self._request(f"{BASE_API_URL}/chapter/{chapter_id}")
            images_data = images_resp.json().get("data", {})
            return [
                f"{BASE_UPLOADS_URL}/{img}"
                for img in images_data.get("images", [])
            ]

        except Exception as exc:
            logger.error(
                "Error fetching images for %s chapter %s: %s", slug, chapter, exc
            )
            return []

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _format_date(self, raw: str) -> str:
        """Formate une date ISO 8601 en 'DD/MM/YYYY'. Retourne '' si invalide."""
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y")
        except Exception:
            return raw[:10] if len(raw) >= 10 else raw


# ------------------------------------------------------------------ #
#  Singleton d'application                                             #
# ------------------------------------------------------------------ #

_phenix_api_instance: PhenixScansAPI | None = None


def get_phenix_api() -> PhenixScansAPI:
    """Retourne l'instance singleton du client Phenix Scans."""
    global _phenix_api_instance
    if _phenix_api_instance is None:
        _phenix_api_instance = PhenixScansAPI()
    return _phenix_api_instance


async def close_phenix_api() -> None:
    """Ferme proprement le client HTTP. À appeler au shutdown de l'app."""
    global _phenix_api_instance
    if _phenix_api_instance is not None:
        await _phenix_api_instance.close()
        _phenix_api_instance = None
