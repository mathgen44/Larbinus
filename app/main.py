"""Point d'entrée de l'application Larbinus."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.middleware import ContexteRequete, JournalJson, LimitationDebit
from app.outils.registre import RegistreOutils
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.rag.depot import DepotDocuments
from app.rag.embeddings import construire_client
from app.rag.service import ServiceRag
from app.routers import chat as chat_router
from app.routers import conversations as conversations_router
from app.routers import documents as documents_router
from app.routers import models as models_router
from app.routers import outils as outils_router
from app.routers import personas as personas_router
from app.routers import openai as openai_router
from app.storage.db import Database

settings = get_settings()

def _configurer_journal() -> None:
    """Journal en texte lisible, ou en JSON si LOG_FORMAT=json."""
    sortie = logging.StreamHandler()
    if settings.log_format.lower() == "json":
        sortie.setFormatter(JournalJson())
    else:
        sortie.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
        )
    racine = logging.getLogger()
    racine.handlers = [sortie]
    racine.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))


_configurer_journal()
logger = logging.getLogger("larbinus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)

    app.state.settings = settings
    registry = ProviderRegistry(settings)
    app.state.registry = registry

    outils = RegistreOutils(settings)
    app.state.outils = outils
    if outils.noms:
        logger.info("Outils disponibles : %s", ", ".join(outils.noms))

    db = Database(Path(settings.data_dir) / "larbinus.db")
    await db.connect()
    app.state.db = db

    depot = DepotDocuments(db)
    await depot.preparer()
    rag = ServiceRag(depot, construire_client(settings), settings)
    app.state.rag = rag
    if rag.disponible:
        logger.info(
            "RAG actif — embeddings « %s » via %s",
            settings.embedding_model, settings.embedding_provider,
        )
    else:
        logger.info("RAG inactif : aucun service d'embeddings configuré.")

    if registry.names:
        logger.info("Fournisseurs activés : %s", ", ".join(registry.names))
    else:
        logger.warning(
            "Aucun fournisseur configuré. Renseignez au moins OLLAMA_BASE_URL "
            "ou une clé d'API dans le fichier .env."
        )
    logger.info("Données persistées dans %s", settings.data_dir)
    if settings.larbinus_api_key:
        portee = "toutes les routes" if settings.larbinus_protect_ui else "les routes /v1"
        logger.info("Clé d'API exigée sur %s", portee)
    else:
        logger.warning(
            "Aucune clé d'API : Larbinus est ouvert à toute machine pouvant "
            "l'atteindre. Acceptable sur un LAN de confiance seulement."
        )
    if settings.rate_limit_requests > 0:
        logger.info(
            "Limitation de débit : %s requêtes / %s s par adresse",
            settings.rate_limit_requests, settings.rate_limit_window,
        )

    try:
        yield
    finally:
        await registry.aclose()
        await outils.aclose()
        await rag.aclose()
        await db.close()
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

# L'ordre compte, et il est contre-intuitif : le **dernier** ajouté est le plus
# extérieur, donc le premier traversé. `ContexteRequete` doit donc être ajouté
# en dernier pour envelopper la limitation de débit — sans quoi un rejet 429
# repartirait sans identifiant de requête ni ligne de journal d'accès.
app.add_middleware(
    LimitationDebit,
    limite=settings.rate_limit_requests,
    fenetre=settings.rate_limit_window,
    proxys=settings.trusted_proxy_set,
)
app.add_middleware(ContexteRequete)


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


STATIC_DIR = Path(__file__).parent / "static"
INDEX = STATIC_DIR / "index.html"

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", tags=["système"], include_in_schema=False)
async def root():
    """Interface de chat, ou description JSON si les fichiers statiques manquent."""
    if INDEX.is_file():
        # no-cache : l'interface est servie par le conteneur, une mise à jour
        # de l'image ne doit pas rester masquée par le cache du navigateur.
        return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})
    return JSONResponse(
        {
            "name": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "health": "/health",
            "message": "Interface absente de l'image.",
        }
    )


# --------------------------------------------------------------------------- #
#  Routeurs
# --------------------------------------------------------------------------- #
app.include_router(models_router.router)   # /api/models, /api/providers
app.include_router(chat_router.router)     # /api/chat
app.include_router(conversations_router.router)  # /api/conversations
app.include_router(personas_router.router)       # /api/personas
app.include_router(documents_router.router)      # /api/documents
app.include_router(outils_router.router)         # /api/outils
app.include_router(openai_router.router)   # /v1/chat/completions, /v1/models
