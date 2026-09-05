"""Point d'entrée de l'application Larbinus."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("larbinus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    providers = settings.enabled_providers
    if providers:
        logger.info("Fournisseurs activés : %s", ", ".join(providers))
    else:
        logger.warning(
            "Aucun fournisseur configuré. Renseignez au moins OLLAMA_BASE_URL "
            "ou une clé d'API dans le fichier .env."
        )
    logger.info("Données persistées dans %s", settings.data_dir)
    yield
    logger.info("Arrêt de %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="Mini assistant IA — passerelle unifiée vers des modèles locaux et en ligne.",
    lifespan=lifespan,
)

# Note : « Access-Control-Allow-Origin: * » et les credentials sont incompatibles côté
# navigateur. Larbinus s'authentifie par en-tête (X-API-Key / Bearer), pas par cookie :
# on n'active donc les credentials que si une liste d'origines explicite est fournie.
_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["système"])
async def health() -> JSONResponse:
    """Sonde de vivacité, utilisée par le HEALTHCHECK Docker."""
    return JSONResponse(
        {
            "status": "ok",
            "name": settings.app_name,
            "version": settings.version,
            "providers": settings.enabled_providers,
        }
    )


@app.get("/", tags=["système"])
async def root() -> JSONResponse:
    """Racine — remplacée par l'interface de chat en phase 4."""
    return JSONResponse(
        {
            "name": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "health": "/health",
            "message": "Socle opérationnel. Interface de chat à venir (phase 4).",
        }
    )
