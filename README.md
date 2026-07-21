# Manga Reader FastAPI

Application FastAPI qui récupère les données depuis `https://demonicscans.org` uniquement lorsqu'un utilisateur consulte une page. Les données sont conservées dans un cache mémoire temporaire avec expiration.

Le projet ne contourne pas les protections anti-bot, les paywalls ou les restrictions du site source. Les requêtes sont de simples appels HTTP avec `requests`.

## Installation

```bash
cd /home/ubuntu/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Lancement

```bash
cd /home/ubuntu/app
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080 --reload

```

Puis ouvrir :

```text
http://127.0.0.1:8000
```
```

## Architecture

```text
app/
├── main.py
├── cache.py
├── scraper/
│   ├── client.py
│   └── parser.py
├── routes/
│   ├── home.py
│   ├── manga.py
│   └── reader.py
├── templates/
│   ├── index.html
│   ├── manga.html
│   └── reader.html
└── static/
    └── style.css
```

## Fonctionnement

- `GET /` charge `https://demonicscans.org/lastupdates.php`, parse les blocs `div.updates-element`, puis affiche les dernières sorties.
- `GET /?list=2` charge `https://demonicscans.org/lastupdates.php?list=2` pour gérer la pagination des dernières sorties.
- `GET /manga/{slug}` charge `https://demonicscans.org/manga/{slug}`, parse le titre, la couverture, le résumé, les informations et les chapitres.
- `GET /read/{slug}/{chapter}` retrouve l'URL source du chapitre depuis la page manga, charge `chaptered.php?manga=ID&chapter=X`, parse les images `img.imgholder`, puis affiche un lecteur vertical.

## Cache

Le cache est en mémoire dans `app/cache.py`.

- Accueil : 5 minutes
- Manga : 10 minutes
- Chapitre : 30 minutes

Si une donnée est déjà en cache et non expirée, l'application ne relance pas de scraping.

## Scraper

Le scraper est indépendant de FastAPI :

- `app/scraper/client.py` contient le client HTTP, le User-Agent navigateur, le timeout et les erreurs réseau.
- `app/scraper/parser.py` contient `parse_home()`, `parse_manga()` et `parse_chapter()`.

Les routes FastAPI ne font qu'orchestrer le cache, le client HTTP, les parsers et les templates.

## Interface

Les templates conservent les routes et les objets existants. Le dossier `app/kakalot/` sert uniquement de référence visuelle locale pour comprendre une mise en page de lecteur manga dense : header compact, navigation horizontale, colonne principale d'updates et colonne latérale. Aucun code HTML/CSS de ce dossier n'est réutilisé.
