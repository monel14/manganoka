"""Types de données partagés (modèle MangaNoka — source unique : Phenix Scans).

Ces TypedDict décrivent la forme des données manipulées par les routes,
le cache et les services. Ils remplacent les anciens types du scraper
MangaBats (supprimé).
"""
from __future__ import annotations

from typing import TypedDict


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
    synopsis: str
    author: str
    status: str
    rating: float
    genres: list[str]
    chapters: list[ChapterLink]
    chapter: ChapterLink


class ChapterPage(TypedDict):
    title: str
    chapter: str
    images: list[str]


class SearchManga(TypedDict):
    title: str
    slug: str
    cover: str
    latest_chapter: str
    views: str
