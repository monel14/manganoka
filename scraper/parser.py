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
    soup = BeautifulSoup(html, "html.parser")
    mangas: list[HomeManga] = []

    for element in soup.select("div.updates-element"):
        manga_link = _first_tag(element, "h2 a[href], .thumb a[href], a[href*='/manga/']")
        title = _text(_first_tag(element, "h2 a")) or _text(manga_link)
        url = _absolute(_attr(manga_link, "href"), base_url)
        slug = _slug_from_url(url)
        cover = _absolute(_attr(_first_tag(element, ".thumb img, img"), "src"), base_url)
        chapters = _chapter_links(element, base_url)
        dates = [item["date"] for item in chapters if item["date"]]

        if title or url:
            mangas.append(
                {
                    "title": title,
                    "slug": slug,
                    "url": url,
                    "cover": cover,
                    "chapters": chapters,
                    "dates": dates,
                }
            )

    return mangas


def parse_manga(html: str, slug: str = "", base_url: str = BASE_URL) -> MangaDetail:
    soup = BeautifulSoup(html, "html.parser")
    stats = _first_tag(soup, "#manga-info-stats")

    return {
        "title": _text(_first_tag(soup, "h1.big-fat-titles, h1")),
        "slug": slug,
        "cover": _cover_url(soup, base_url),
        "description": _description(soup),
        "author": _stat_value(stats, ("author", "auteur", "artist", "artiste")),
        "status": _stat_value(stats, ("status", "statut")),
        "rating": _rating(soup, stats),
        "chapters": _chapter_links(_first_tag(soup, "#chapters-list") or soup, base_url),
    }


def parse_chapter(
    html: str,
    title: str = "",
    chapter: str = "",
    base_url: str = BASE_URL,
) -> ChapterPage:
    soup = BeautifulSoup(html, "html.parser")
    images = [
        _absolute(_attr(img, "src") or _attr(img, "data-src"), base_url)
        for img in soup.select("img.imgholder")
    ]
    images = [image for image in images if image]

    return {
        "title": title or _text(_first_tag(soup, "title")),
        "chapter": chapter,
        "images": images,
    }


def _chapter_links(container: Tag, base_url: str) -> list[ChapterLink]:
    chapters: list[ChapterLink] = []
    seen_urls: set[str] = set()

    # Priority 1 : .chap-date rows (page d'accueil demonicscans)
    for row in container.select(".chap-date"):
        chapter = _chapter_from_row(row, base_url)
        if chapter and chapter["url"] not in seen_urls:
            seen_urls.add(chapter["url"])
            chapters.append(chapter)

    # Priority 2 : a.chplinks inside #chapters-list (page manga demonicscans)
    # Only if no .chap-date rows were found (page manga n'a pas de .chap-date)
    if not chapters:
        for link in container.select("a.chplinks[href*='chaptered.php']"):
            href = _attr(link, "href")
            url = _absolute(href, base_url)
            if url in seen_urls:
                continue
            number = _chapter_number(href)
            span = link.find("span")
            date = _text(span) if isinstance(span, Tag) else ""
            raw_title = link.get_text(" ", strip=True)
            if date and raw_title.endswith(date):
                raw_title = raw_title[: -len(date)].strip()
            title = raw_title or f"Chapter {number}"
            chapters.append({"number": number, "title": title, "url": url, "date": date})
            seen_urls.add(url)

    return _dedupe_chapters(chapters)


def _chapter_from_row(row: Tag, base_url: str) -> ChapterLink | None:
    # demonicscans structure:
    # <div class="chap-date flex flex-row justify-space-between">
    #   <div><a class="chplinks" href="...">Chapter X</a></div>
    #   <div><a class="chplinks" href="...">2026-07-15</a></div>
    # </div>
    divs = row.find_all("div", recursive=False)
    if len(divs) >= 2:
        chapter_link = divs[0].find("a")
        date_link = divs[1].find("a")
        if not chapter_link:
            return None
        href = _attr(chapter_link, "href")
        number = _chapter_number(href) or _chapter_number(_text(chapter_link))
        date = _text(date_link) if date_link else ""
        return {
            "number": number,
            "title": _text(chapter_link) or f"Chapter {number}",
            "url": _absolute(href, base_url),
            "date": date,
        }

    # fallback: old format with a single link + date text
    links = [l for l in row.select("a[href*='chaptered.php']") if isinstance(l, Tag)]
    if not links:
        return None
    chapter_link = next((l for l in links if not _looks_like_date(_text(l))), links[0])
    href = _attr(chapter_link, "href")
    number = _chapter_number(href) or _chapter_number(_text(chapter_link))
    date = next((_text(l) for l in links if _looks_like_date(_text(l))), "")
    return {
        "number": number,
        "title": _text(chapter_link) or f"Chapter {number}",
        "url": _absolute(href, base_url),
        "date": date,
    }


def _dedupe_chapters(chapters: list[ChapterLink]) -> list[ChapterLink]:
    seen: set[tuple[str, str]] = set()
    unique: list[ChapterLink] = []

    for chapter in chapters:
        key = (chapter["number"], chapter["url"])
        if key not in seen:
            seen.add(key)
            unique.append(chapter)

    return unique


def _cover_url(soup: BeautifulSoup, base_url: str) -> str:
    selectors = [
        "meta[property='og:image']",
        ".thumb img",
        "#manga-info img",
        ".manga-info img",
        "img[src*='cover']",
    ]

    for selector in selectors:
        tag = _first_tag(soup, selector)
        value = _attr(tag, "content") or _attr(tag, "src")
        if value:
            return _absolute(value, base_url)

    return ""


def _description(soup: BeautifulSoup) -> str:
    candidates = soup.select("div.white-font")
    for candidate in candidates:
        text = _text(candidate)
        if len(text) > 30:
            return text
    return _text(candidates[0]) if candidates else ""


def _rating(soup: BeautifulSoup, stats: Tag | None) -> str:
    rating = _stat_value(stats, ("rating", "note"))
    if rating:
        return rating

    tag = _first_tag(soup, "[class*='rating'], [id*='rating']")
    return _text(tag)


def _stat_value(stats: Tag | None, labels: tuple[str, ...]) -> str:
    if stats is None:
        return ""

    for row in stats.find_all(["div", "p", "li", "span"], recursive=True):
        value = _value_from_text(_text(row), labels)
        if value:
            return value

    return _value_from_text(_text(stats), labels)


def _value_from_text(text: str, labels: tuple[str, ...]) -> str:
    if not text:
        return ""

    for label in labels:
        pattern = rf"\b{re.escape(label)}\b\s*:?\s*([^|\n\r]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" :-")

    return ""


def _near_date(link: Tag) -> str:
    for selector in ("time", ".date", ".chapter-date", ".chapterdate"):
        date_tag = _first_tag(link.parent if isinstance(link.parent, Tag) else link, selector)
        date = _attr(date_tag, "datetime") or _text(date_tag)
        if date:
            return date

    parent = link.parent
    grandparent = parent.parent if isinstance(parent, Tag) else None
    if isinstance(grandparent, Tag):
        for candidate in grandparent.select("a, time, span, i"):
            text = _text(candidate)
            if candidate is not link and _looks_like_date(text):
                return text

    return ""


def _looks_like_date(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
        or re.search(r"\b\d{2}-\d{2}\s+\d{2}:\d{2}\b", text)
        or re.search(r"\b\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago\b", text, re.I)
    )


def _chapter_number(value: str) -> str:
    if not value:
        return ""

    query = parse_qs(urlparse(value).query)
    if "chapter" in query and query["chapter"]:
        return query["chapter"][0]

    match = re.search(r"(?:chapter|chapitre|ch\.?)\s*([0-9]+(?:\.[0-9]+)?)", value, re.I)
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


def parse_search(html: str, base_url: str = BASE_URL) -> list[SearchManga]:
    """Parse search results - handles both full page and dropdown fragment."""
    soup = BeautifulSoup(html, "html.parser")
    mangas: list[SearchManga] = []

    # demonicscans returns a dropdown fragment: #result-box-bg > a > li
    for link in soup.select("a[href]"):
        href = _attr(link, "href")
        if not href or "/manga/" not in href:
            continue

        title_div = link.select_one("li div div:first-child")
        title = _text(title_div) if title_div else _text(link)
        if not title:
            continue

        # href may be relative (/manga/Slug) or absolute
        url = href if href.startswith("http") else _absolute(href, base_url)
        slug = _slug_from_url(url)

        cover_tag = link.select_one("img.search-thumb, img")
        # covers may be hosted on a different domain — use as-is if already absolute
        raw_cover = _attr(cover_tag, "src")
        cover = raw_cover if raw_cover.startswith("http") else _absolute(raw_cover, base_url)

        # views are in the second div inside the flex column
        divs = link.select("li .seach-right div, li .flex.flex-col div")
        views = ""
        if len(divs) >= 2:
            raw = _text(divs[-1])
            # strip SVG text residue, keep only digits and spaces
            views = re.sub(r"[^\d\s]", "", raw).strip()

        mangas.append(
            {
                "title": title,
                "slug": slug,
                "url": url,
                "cover": cover,
                "latest_chapter": "",
                "views": views,
            }
        )

    return mangas