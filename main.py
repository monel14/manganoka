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
from services.phenix_scans import close_phenix_api
from services.phenix_scans import close_phenix_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    await close_phenix_api()
    await close_phenix_api()


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
        "error_title": "Noka Got Lost...",
        "error_message": "😵 This page doesn't exist or has been moved. "
                         "Noka searched every shelf in the library, but couldn't find it!",
        "noka_image": "/noka_lost.svg",
    },
    500: {
        "error_title": "Noka Fell Asleep...",
        "error_message": "😴 Something went wrong on our end. "
                         "Give Noka a moment to wake up, then try again.",
        "noka_image": "/noka_sleep.svg",
    },
    502: {
        "error_title": "Noka Fell Asleep...",
        "error_message": "😴 The library isn't responding right now. "
                         "Please try again in a few moments.",
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

# Routes d'administration
from routes import admin
app.include_router(admin.router)

# 5. Routes pour les fichiers favicon à la racine du domaine
#    (indispensable pour que Google affiche le favicon dans les résultats)
from fastapi.responses import FileResponse

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse(BASE_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/favicon-16x16.png", include_in_schema=False)
async def favicon_16():
    return FileResponse(BASE_DIR / "favicon-16x16.png", media_type="image/png")

@app.get("/favicon-32x32.png", include_in_schema=False)
async def favicon_32():
    return FileResponse(BASE_DIR / "favicon-32x32.png", media_type="image/png")

@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse(BASE_DIR / "apple-touch-icon.png", media_type="image/png")

@app.get("/android-chrome-192x192.png", include_in_schema=False)
async def android_chrome_192():
    return FileResponse(BASE_DIR / "android-chrome-192x192.png", media_type="image/png")

@app.get("/android-chrome-512x512.png", include_in_schema=False)
async def android_chrome_512():
    return FileResponse(BASE_DIR / "android-chrome-512x512.png", media_type="image/png")

# ------------------------------------------------------------------ #
#  Version française sous /fr : redirections depuis les anciens chemins #
# ------------------------------------------------------------------ #
from fastapi.responses import RedirectResponse
from urllib.parse import quote

@app.get("/", include_in_schema=False)
async def fr_root_redirect():
    return RedirectResponse("/fr", status_code=307)

@app.get("/manga/{slug}", include_in_schema=False)
async def fr_legacy_manga(slug: str):
    return RedirectResponse(f"/fr/manga/{slug}", status_code=301)

@app.get("/read/{slug}/{chapter}", include_in_schema=False)
async def fr_legacy_reader(slug: str, chapter: str):
    return RedirectResponse(f"/fr/read/{slug}/{chapter}", status_code=301)

@app.get("/search", include_in_schema=False)
async def fr_legacy_search(request: Request):
    q = request.query_params.get("q", "")
    p = request.query_params.get("p", "")
    target = f"/fr/search?q={quote(q)}" + (f"&p={p}" if p else "")
    return RedirectResponse(target, status_code=301)

@app.get("/history", include_in_schema=False)
async def fr_legacy_history():
    return RedirectResponse("/fr/history", status_code=301)

@app.get("/privacy-policy", include_in_schema=False)
async def fr_legacy_privacy():
    return RedirectResponse("/fr/privacy-policy", status_code=301)

@app.get("/terms-conditions", include_in_schema=False)
async def fr_legacy_terms():
    return RedirectResponse("/fr/terms-conditions", status_code=301)

@app.get("/genre/{genre_slug}", include_in_schema=False)
async def fr_legacy_genre(genre_slug: str):
    return RedirectResponse("/fr", status_code=301)

@app.get("/manga-list/{list_type}", include_in_schema=False)
async def fr_legacy_manga_list(list_type: str):
    return RedirectResponse("/fr", status_code=301)

@app.get("/manifest.json", include_in_schema=False)
async def manifest_json():
    return FileResponse(BASE_DIR / "static" / "manifest.json", media_type="application/manifest+json")
