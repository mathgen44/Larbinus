"""Point d'entrée de l'application Larbinus."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.routers import chat as chat_router
from app.routers import models as models_router
from app.routers import openai as openai_router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("larbinus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)

    registry = ProviderRegistry(settings)
    app.state.registry = registry

    if registry.names:
        logger.info("Fournisseurs activés : %s", ", ".join(registry.names))
    else:
        logger.warning(
            "Aucun fournisseur configuré. Renseignez au moins OLLAMA_BASE_URL "
            "ou une clé d'API dans le fichier .env."
        )
    logger.info("Données persistées dans %s", settings.data_dir)

    try:
        yield
    finally:
        await registry.aclose()
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


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    """Traduit toute erreur de fournisseur en réponse HTTP cohérente."""
    logger.warning("Erreur fournisseur sur %s : %s", request.url.path, exc)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# --------------------------------------------------------------------------- #
#  Système
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["système"])
async def health(request: Request) -> JSONResponse:
    """Sonde de vivacité, utilisée par le HEALTHCHECK Docker.

    Volontairement locale : elle ne contacte aucun fournisseur, pour qu'une API
    externe en panne ne fasse pas redémarrer le conteneur en boucle.
    """
    registry: ProviderRegistry = request.app.state.registry
    return JSONResponse(
        {
            "status": "ok",
            "name": settings.app_name,
            "version": settings.version,
            "providers": registry.names,
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


# --------------------------------------------------------------------------- #
#  Routeurs
# --------------------------------------------------------------------------- #
app.include_router(models_router.router)   # /api/models, /api/providers
app.include_router(chat_router.router)     # /api/chat
app.include_router(openai_router.router)   # /v1/chat/completions, /v1/models
