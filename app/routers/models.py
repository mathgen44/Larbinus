"""Routes de découverte : modèles disponibles et état des fournisseurs."""

from fastapi import APIRouter, Depends, Request

from app.providers.registry import ProviderRegistry
from app.schemas import ModelInfo, ProviderStatus
from app.security import require_api_key

router = APIRouter(prefix="/api", tags=["modèles"], dependencies=[Depends(require_api_key)])


@router.get("/models", response_model=list[ModelInfo])
async def list_models(request: Request) -> list[ModelInfo]:
    """Modèles de tous les fournisseurs actifs, identifiés `fournisseur/modèle`.

    Un fournisseur injoignable est ignoré plutôt que de faire échouer l'appel :
    l'interface reste utilisable si une seule API est en panne.
    """
    registry: ProviderRegistry = request.app.state.registry
    return await registry.list_models()


@router.get("/providers", response_model=list[ProviderStatus])
async def list_providers(request: Request) -> list[ProviderStatus]:
    """État de chaque fournisseur configuré — utile pour diagnostiquer une panne."""
    registry: ProviderRegistry = request.app.state.registry
    return await registry.statuses()
