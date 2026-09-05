"""Gestion de la base documentaire du RAG."""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.rag.extraction import EXTENSIONS
from app.rag.service import ServiceRag
from app.security import require_api_key

logger = logging.getLogger("larbinus.documents")

router = APIRouter(
    prefix="/api/documents",
    tags=["documents"],
    dependencies=[Depends(require_api_key)],
)


def service(request: Request) -> ServiceRag:
    return request.app.state.rag


@router.get("")
async def lister(request: Request) -> dict:
    """Documents indexés et état général de l'index."""
    rag = service(request)
    return {
        "disponible": rag.disponible,
        "index": await rag.depot.etat_index(),
        "formats": sorted(EXTENSIONS),
        "documents": await rag.depot.documents(),
    }


@router.post("", status_code=201)
async def deposer(request: Request, files: list[UploadFile] = File(...)) -> dict:
    """Dépôt d'un ou plusieurs fichiers, indexés dans la foulée."""
    rag = service(request)
    resultats = []

    for fichier in files:
        donnees = await fichier.read()
        if not donnees:
            resultats.append({"filename": fichier.filename, "erreur": "Fichier vide."})
            continue
        if len(donnees) > rag.settings.max_document_bytes:
            limite = rag.settings.max_document_bytes // (1024 * 1024)
            resultats.append(
                {"filename": fichier.filename, "erreur": f"Fichier trop volumineux (> {limite} Mo)."}
            )
            continue
        resultats.append(await rag.deposer(fichier.filename or "sans-nom", donnees))

    return {"disponible": rag.disponible, "resultats": resultats}


@router.post("/scan")
async def scanner(request: Request) -> dict:
    """Indexe le contenu du dossier surveillé (`DOCUMENTS_DIR`)."""
    return await service(request).scanner()


@router.post("/reindexer-echecs")
async def reindexer_echecs(request: Request) -> dict:
    """Réessaie tous les documents en erreur ou en attente.

    Utile juste après un `ollama pull` du modèle d'embedding manquant.
    """
    return await service(request).reindexer_les_echecs()


@router.post("/{identifiant}/reindexer")
async def reindexer(request: Request, identifiant: str) -> dict:
    document = await service(request).reindexer(identifiant)
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return document


@router.delete("/{identifiant}", status_code=204, response_class=Response)
async def supprimer(request: Request, identifiant: str):
    if not await service(request).supprimer(identifiant):
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return Response(status_code=204)


@router.post("/reinitialiser")
async def reinitialiser(request: Request) -> dict:
    """Vide l'index sans supprimer les documents — utile après un changement
    de modèle d'embedding, qui rend les vecteurs existants incomparables."""
    rag = service(request)
    await rag.depot.reinitialiser_index()
    return await rag.depot.etat_index()


@router.get("/recherche")
async def rechercher(
    request: Request,
    q: str = Query(..., min_length=2, description="Question ou mots-clés"),
    limite: int = Query(5, ge=1, le=20),
) -> dict:
    """Recherche sémantique brute — pratique pour vérifier ce que le modèle verra."""
    rag = service(request)
    if not rag.disponible:
        raise HTTPException(
            status_code=503,
            detail="Aucun service d'embeddings configuré (EMBEDDING_PROVIDER).",
        )
    return {"question": q, "resultats": await rag.rechercher(q, limite)}
