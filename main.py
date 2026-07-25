import os
from pathlib import Path
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# 1. Chargement immédiat des variables d'environnement depuis le dossier app
dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Import de tes routes modulaires
from routes import home, manga, reader, search, images
from scraper.client import close_http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    await close_http_client()


app = FastAPI(title="MangaNoka", lifespan=lifespan)

# Définition du dossier de base absolu
BASE_DIR = Path(__file__).resolve().parent

# Créer automatiquement le dossier static s'il n'existe pas pour éviter les plantages de Starlette
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)

# 2. Configuration des dossiers statiques et templates
app.mount(
    "/static",
    StaticFiles(directory=static_dir),
    name="static",
)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# 3. Pages d'erreur brandées avec la mascotte Noka
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_ERROR_CONTENT = {
    404: {
        "error_title": "Noka s'est égaré...",
        "error_message": "😵 Cette page n'existe pas ou a été déplacée. "
                         "Noka a fouillé tous les rayons de la bibliothèque, sans succès !",
        "noka_image": "/noka_lost.svg",
    },
    500: {
        "error_title": "Noka s'est endormi...",
        "error_message": "😴 Quelque chose s'est mal passé de notre côté. "
                         "Laisse Noka se réveiller et réessaie dans un instant.",
        "noka_image": "/noka_sleep.svg",
    },
    502: {
        "error_title": "Noka s'est endormi...",
        "error_message": "😴 La bibliothèque ne répond pas pour le moment. "
                         "Réessaie dans quelques instants.",
        "noka_image": "/noka_sleep.svg",
    },
}


@app.exception_handler(StarletteHTTPException)
async def branded_http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Les routes d'API/images gardent une réponse JSON légère
    path = request.url.path
    wants_html = "text/html" in (request.headers.get("accept") or "")
    is_api = path.startswith(("/img-proxy", "/api", "/static"))

    if wants_html and not is_api:
        content = _ERROR_CONTENT.get(exc.status_code, _ERROR_CONTENT[404])
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "status_code": exc.status_code, **content},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def branded_server_error_handler(request: Request, exc: Exception):
    wants_html = "text/html" in (request.headers.get("accept") or "")
    if wants_html:
        content = _ERROR_CONTENT[500]
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "status_code": 500, **content},
            status_code=500,
        )
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


# 4. Inclusion de tous les routeurs
app.include_router(home.router)
app.include_router(manga.router)
app.include_router(reader.router)
app.include_router(search.router)
app.include_router(images.router)  # Ta nouvelle route proxy/cache d'images