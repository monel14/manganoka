from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)

BASE_API_URL = "https://api.phenix-scans.co/api"
BASE_UPLOADS_URL = "https://api.phenix-scans.co"
TIMEOUT = 30.0


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
    author: str
    status: str
    rating: float
    genres: list[str]
    chapters: list[ChapterDict]


class PhenixScansAPI:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=TIMEOUT)

    async def close(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ #
    #  Page d'accueil                                                      #
    # ------------------------------------------------------------------ #

    async def get_latest_mangas(
        self, page: int = 1, limit: int = 12
    ) -> tuple[list[MangaDict], int]:
        """Retourne les derniers mangas mis à jour avec leurs 3 derniers chapitres."""
        try:
            response = await self.client.get(
                f"{BASE_API_URL}/manga",
                params={"page": page, "limit": limit},
            )
            response.raise_for_status()
            data = response.json().get("data", [])

            # Semaphore à 2 pour lisser les requêtes parallèles et éviter les 429
            sem = asyncio.Semaphore(2)

            async def fetch_chapters_for_manga(item: dict) -> list[ChapterDict]:
                async with sem:
                    manga_id = item.get("id", "")
                    slug = item.get("slug", "")
                    chapters: list[ChapterDict] = []
                    if not manga_id:
                        return chapters

                    await asyncio.sleep(0.1)  # Décalage de 100 ms entre requêtes

                    retries, backoff = 3, 0.5
                    for attempt in range(retries):
                        try:
                            ch_resp = await self.client.get(
                                f"{BASE_API_URL}/manga/{manga_id}/chapters"
                            )
                            if ch_resp.status_code == 429:
                                await asyncio.sleep(backoff)
                                backoff *= 2.0
                                continue

                            ch_resp.raise_for_status()
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
                                        "url": f"/read/{slug}/{ch_num}",
                                        "date": self._format_date(
                                            ch.get("createdAt", "")
                                        ),
                                    }
                                )
                            break  # Succès
                        except Exception:
                            if attempt == retries - 1:
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
            return mangas, 1

        except Exception as exc:
            logger.error("Error fetching latest mangas: %s", exc)
            return [], 1

    # ------------------------------------------------------------------ #
    #  Fiche manga                                                         #
    # ------------------------------------------------------------------ #

    async def get_manga_detail(self, slug: str) -> MangaDict | None:
        """Retourne la fiche complète d'un manga avec tous ses chapitres."""
        try:
            response = await self.client.get(f"{BASE_API_URL}/manga/{slug}")
            response.raise_for_status()
            data = response.json().get("data", {})
            manga_id = data.get("id", "")

            chapters: list[ChapterDict] = []
            if manga_id:
                try:
                    ch_resp = await self.client.get(
                        f"{BASE_API_URL}/manga/{manga_id}/chapters"
                    )
                    ch_resp.raise_for_status()
                    chapters_data = ch_resp.json().get("data", [])

                    for ch in chapters_data:
                        ch_num = str(ch.get("number", "1")).rstrip(".0") or "1"
                        chapters.append(
                            {
                                "number": ch_num,
                                "title": f"Chapitre {ch_num}",
                                "url": f"/read/{slug}/{ch_num}",
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
            manga_resp = await self.client.get(f"{BASE_API_URL}/manga/{slug}")
            manga_resp.raise_for_status()
            manga_id = manga_resp.json().get("data", {}).get("id")
            if not manga_id:
                return []

            # 2. Liste des chapitres
            ch_list_resp = await self.client.get(
                f"{BASE_API_URL}/manga/{manga_id}/chapters"
            )
            ch_list_resp.raise_for_status()
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
            images_resp = await self.client.get(f"{BASE_API_URL}/chapter/{chapter_id}")
            images_resp.raise_for_status()
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
