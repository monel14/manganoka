from __future__ import annotations

import re
from typing import TypedDict
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.client import BASE_URL


class ChapterLink(TypedDict):
    number: str
    title: str
    url: str
    date: str


class HomeManga(TypedDict):
    title: str
    slug: str
    url: str
    cover: str
    chapters: list[ChapterLink]
    dates: list[str]


class MangaDetail(TypedDict):
    title: str
    slug: str
    cover: str
    description: str
    author: str
    status: str
    rating: str
    chapters: list[ChapterLink]


class ChapterPage(TypedDict):
    title: str
    chapter: str
    images: list[str]


class SearchManga(TypedDict):
    title: str
    slug: str
    url: str
    cover: str
    latest_chapter: str
    views: str


def parse_home(html: str, base_url: str = BASE_URL) -> list[HomeManga]:
    """Parse la page d'accueil de MangaBats (itemupdate)."""
    soup = BeautifulSoup(html, "html.parser")
    mangas: list[HomeManga] = []

    for element in soup.select("div.itemupdate"):
        # Ignorer les bannières publicitaires cachées ou modèles AI
        if "js-banner-ai-home" in element.get("class", []):
            continue
            
        cover_a = element.select_one("a.cover")
        if not cover_a:
            continue
            
        title_a = element.select_one("h3 a")
        title = title_a.text.strip() if title_a else ""
        url = _absolute(cover_a.get("href") or title_a.get("href"), base_url)
        slug = _slug_from_url(url)
        
        img = cover_a.select_one("img")
        cover = _absolute(img.get("src") or img.get("data-src"), base_url) if img else ""
        
        # Récupérer les chapitres listés sous le manga (généralement 3 max)
        chapters: list[ChapterLink] = []
        for li in element.select("ul li")[1:]:  # Le premier li est le h3 du titre
            ch_a = li.select_one("a")
            if ch_a:
                ch_href = ch_a.get("href")
                ch_url = _absolute(ch_href, base_url)
                ch_num = _chapter_number(ch_href)
                ch_title = ch_a.text.strip()
                
                date_i = li.select_one("i")
                ch_date = date_i.text.strip() if date_i else ""
                
                chapters.append({
                    "number": ch_num,
                    "title": ch_title,
                    "url": ch_url,
                    "date": ch_date
                })
        
        if title or url:
            mangas.append({
                "title": title,
                "slug": slug,
                "url": url,
                "cover": cover,
                "chapters": chapters,
                "dates": [item["date"] for item in chapters if item["date"]],
            })

    return mangas


def parse_popular(html: str, base_url: str = BASE_URL) -> list[dict]:
    """Parse la section 'POPULAR MANGA' du carrousel de MangaBats."""
    soup = BeautifulSoup(html, "html.parser")
    popular: list[dict] = []

    for item in soup.select(".slide .owl-carousel .item"):
        img_tag = item.select_one("img")
        cover = _absolute(img_tag.get("src") or img_tag.get("data-src"), base_url) if img_tag else ""
        
        title_a = item.select_one(".slide-caption h3 a")
        title = title_a.text.strip() if title_a else ""
        url = _absolute(title_a.get("href"), base_url) if title_a else ""
        slug = _slug_from_url(url)
        
        ch_a = item.select_one('.slide-caption a[href*="/chapter-"]')
        ch_title = ch_a.text.strip() if ch_a else ""
        ch_url = _absolute(ch_a.get("href"), base_url) if ch_a else ""
        ch_num = _chapter_number(ch_url)
        
        popular.append({
            "title": title,
            "slug": slug,
            "url": url,
            "cover": cover,
            "chapter": {
                "title": ch_title,
                "url": ch_url,
                "number": ch_num,
            }
        })
        
    return popular


def parse_popular_sidebar(html: str, base_url: str = BASE_URL) -> list[dict]:
    """Parse la section 'Most Popular Manga' de la barre latérale de MangaBats."""
    soup = BeautifulSoup(html, "html.parser")
    popular_sidebar: list[dict] = []

    for item in soup.select(".xem-nhieu .xem-nhieu-item"):
        title_a = item.select_one("h3 a")
        if title_a:
            title = title_a.text.strip()
            url = _absolute(title_a.get("href"), base_url)
            slug = _slug_from_url(url)
            popular_sidebar.append({
                "title": title,
                "slug": slug,
                "url": url,
            })
            
    return popular_sidebar


def parse_manga_list(html: str, base_url: str = BASE_URL) -> list[HomeManga]:
    """Parse une liste de mangas au format grille cPanel (utilisé par /manga-list/ et /genre/)."""
    soup = BeautifulSoup(html, "html.parser")
    mangas: list[HomeManga] = []

    for element in soup.select(".list-comic-item-wrap"):
        # Ignorer les bannières publicitaires
        if "_098cc7f8" in element.get("class", []):
            continue
            
        cover_a = element.select_one("a.cover")
        if not cover_a:
            continue
            
        title_a = element.select_one("h3 a")
        title = title_a.text.strip() if title_a else ""
        url = _absolute(cover_a.get("href") or title_a.get("href"), base_url)
        slug = _slug_from_url(url)
        
        img = cover_a.select_one("img")
        cover = _absolute(img.get("src") or img.get("data-src"), base_url) if img else ""
        
        # Récupérer le dernier chapitre
        chapters: list[ChapterLink] = []
        ch_a = element.select_one("a.list-story-item-wrap-chapter")
        if ch_a:
            ch_href = ch_a.get("href")
            ch_url = _absolute(ch_href, base_url)
            ch_title = ch_a.text.strip()
            ch_num = _chapter_number(ch_href)
            chapters.append({
                "number": ch_num,
                "title": ch_title,
                "url": ch_url,
                "date": "New"
            })
            
        mangas.append({
            "title": title,
            "slug": slug,
            "url": url,
            "cover": cover,
            "chapters": chapters,
            "dates": ["New"] if chapters else []
        })

    return mangas


def parse_manga(html: str, slug: str = "", chapters_data: dict | None = None, base_url: str = BASE_URL) -> MangaDetail:
    """Parse les détails d'un manga sur sa fiche MangaBats."""
    soup = BeautifulSoup(html, "html.parser")
    
    title_tag = soup.select_one("h1")
    title = title_tag.text.strip() if title_tag else ""
    
    # Couverture
    cover_tag = soup.select_one(".info-image img, .manga-info-pic img")
    cover = _absolute(cover_tag.get("src") or cover_tag.get("data-src"), base_url) if cover_tag else ""
    
    # Description (MangaBats uses #contentBox for summaries)
    desc_tag = soup.select_one(".panel-story-info-description, .story-info-right-description, #panel-story-info-description, #contentBox")
    description = ""
    if desc_tag:
        h2 = desc_tag.select_one("h2")
        if h2:
            h2_text = h2.text.strip()
            description = desc_tag.get_text(" ", strip=True).replace(h2_text, "").strip()
        else:
            description = desc_tag.get_text(" ", strip=True)
            
        if description.lower().startswith("description :"):
            description = description[13:].strip()
            
    # Extraction propre des métadonnées (Auteur, Statut, Rating)
    meta_container = soup.select_one(".story-info-right, .panel-story-info, .manga-info-top")
    meta_text = meta_container.text if meta_container else ""
    
    # Remplacement des labels complexes pour que _value_from_text les parse proprement
    meta_text_clean = meta_text.replace("Author(s)", "Author").replace("Auteur(s)", "Author")
    
    author = _value_from_text(meta_text_clean, ("author", "auteur"))
    status = _value_from_text(meta_text_clean, ("status", "statut"))
    rating = _value_from_text(meta_text_clean, ("rate", "rating", "note"))
    
    # Récupération de la liste des chapitres depuis les données de l'API (passées en argument)
    chapters: list[ChapterLink] = []
    if chapters_data and chapters_data.get("success") and "data" in chapters_data:
        raw_chapters = chapters_data["data"].get("chapters", [])
        for ch in raw_chapters:
            ch_slug = ch.get("chapter_slug", "")
            ch_num = str(ch.get("chapter_num", ""))
            ch_name = ch.get("chapter_name", f"Chapter {ch_num}")
            ch_url = f"{base_url}/manga/{slug}/{ch_slug}"
            
            # Formatage de la date (ex: "2026-07-22T10:51:08.000000Z" -> "2026-07-22")
            raw_date = ch.get("updated_at", "")
            if raw_date and "T" in raw_date:
                ch_date = raw_date.split("T")[0]
            else:
                ch_date = raw_date
                
            chapters.append({
                "number": ch_num,
                "title": ch_name,
                "url": ch_url,
                "date": ch_date
            })
            
    return {
        "title": title,
        "slug": slug,
        "cover": cover,
        "description": description,
        "author": author or "Unknown",
        "status": status or "Ongoing",
        "rating": rating or "N/A",
        "chapters": chapters,
    }


def parse_chapter(
    html: str,
    title: str = "",
    chapter: str = "",
    base_url: str = BASE_URL,
) -> ChapterPage:
    """Parse les images de la liseuse de chapitre sur MangaBats."""
    soup = BeautifulSoup(html, "html.parser")
    images: list[str] = []
    
    # MangaBats liste toutes ses images dans un conteneur dédié '.container-chapter-reader'
    for img in soup.select(".container-chapter-reader img"):
        src = img.get("src") or img.get("data-src")
        if src:
            images.append(_absolute(src, base_url))

    return {
        "title": title or _text(_first_tag(soup, "title")),
        "chapter": chapter,
        "images": images,
    }


def parse_search(html: str, base_url: str = BASE_URL) -> list[SearchManga]:
    """Parse la page de résultats de recherche de MangaBats."""
    soup = BeautifulSoup(html, "html.parser")
    mangas: list[SearchManga] = []

    # Chaque résultat est contenu dans un bloc '.story_item'
    for element in soup.select(".panel_story_list .story_item"):
        title_a = element.select_one("h3.story_name a")
        if not title_a:
            continue
            
        title = title_a.text.strip()
        url = _absolute(title_a.get("href"), base_url)
        slug = _slug_from_url(url)
        
        img = element.select_one("img")
        cover = _absolute(img.get("src") or img.get("data-src"), base_url) if img else ""
        
        # Récupération des vues et de l'auteur dans le texte brut ou les spans
        views = ""
        author = ""
        for span in element.select("span"):
            span_text = span.text
            if "view" in span_text.lower():
                view_match = re.search(r'[\d,]+', span_text)
                if view_match:
                    views = view_match.group(0).replace(",", "").strip()
            elif "author" in span_text.lower():
                author = span_text.split(":", 1)[1].strip() if ":" in span_text else span_text.strip()
            
        latest_chapter = ""
        ch_a = element.select_one("em.story_chapter a")
        if ch_a:
            latest_chapter = ch_a.text.strip()
            
        mangas.append({
            "title": title,
            "slug": slug,
            "url": url,
            "cover": cover,
            "latest_chapter": latest_chapter,
            "views": views,
            "author": author,  # Ajout de l'auteur
        })

    return mangas


def _value_from_text(text: str, labels: tuple[str, ...]) -> str:
    if not text:
        return ""

    for label in labels:
        pattern = rf"\b{re.escape(label)}\b\s*:?\s*([^|\n\r]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" :-")

    return ""


def _chapter_number(value: str) -> str:
    if not value:
        return ""

    match = re.search(r'chapter-([0-9]+(?:\.[0-9]+)?)', value, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\b", value)
    return match.group(1) if match else ""


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if path.startswith("manga/"):
        return path.split("/", 1)[1].strip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _first_tag(root: BeautifulSoup | Tag, selector: str) -> Tag | None:
    found = root.select_one(selector)
    return found if isinstance(found, Tag) else None


def _attr(tag: Tag | None, name: str) -> str:
    if tag is None:
        return ""
    value = tag.get(name)
    return str(value).strip() if value else ""


def _text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    return " ".join(tag.get_text(" ", strip=True).split())


def _absolute(url: str, base_url: str) -> str:
    return urljoin(base_url, url) if url else ""
