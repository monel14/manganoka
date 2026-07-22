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


app = FastAPI(title="Lola Manga Reader", lifespan=lifespan)

# 2. Configuration des dossiers statiques et templates
BASE_DIR = Path(__file__).resolve().parent
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# 3. Inclusion de tous les routeurs
app.include_router(home.router)
app.include_router(manga.router)
app.include_router(reader.router)
app.include_router(search.router)
app.include_router(images.router)  # Ta nouvelle route proxy/cache d'images