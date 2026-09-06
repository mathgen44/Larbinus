"""Outils : catalogue et exécution confirmée.

Cette route est celle par laquelle passe une action que le modèle n'avait pas
le droit de lancer seul. La proposition est **revalidée** ici : l'utilisateur
confirme une intention, pas un blanc-seing. Une machine hors inventaire ou un
chemin hors périmètre reste refusé, même confirmé.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import require_api_key_ui

logger = logging.getLogger("larbinus.outils.api")

router = APIRouter(
    prefix="/api/outils",
    tags=["outils"],
    dependencies=[Depends(require_api_key_ui)],
)


class DemandeExecution(BaseModel):
    outil: str
    parametres: dict[str, str]
    conversation_id: str | None = Field(
        default=None,
        description="Si fourni, le compte rendu est enregistré dans la conversation.",
    )


@router.get("")
async def catalogue(request: Request) -> dict:
    """Outils disponibles sur cette instance, et machines déclarées."""
    registre = request.app.state.outils
    return {"outils": registre.catalogue()}


@router.post("/executer")
async def executer(request: Request, corps: DemandeExecution) -> dict:
    """Exécute une action après confirmation explicite de l'utilisateur."""
    registre = request.app.state.outils
    outil = registre.get(corps.outil)
    if outil is None:
        raise HTTPException(
            status_code=404,
            detail=f"Outil « {corps.outil} » indisponible. "
                   f"Disponibles : {', '.join(registre.noms) or 'aucun'}.",
        )

    proposition = outil.preparer(corps.parametres, brut="")
    if proposition.erreur:
        # Confirmer ne dispense pas de valider : la vérification d'inventaire
        # et de périmètre s'applique aussi à une action approuvée.
        raise HTTPException(status_code=400, detail=proposition.erreur)

    logger.info(
        "Exécution confirmée par l'utilisateur : %s — %s",
        proposition.outil, proposition.resume,
    )
    resultat = await registre.executer(proposition)

    if corps.conversation_id:
        db = request.app.state.db
        if await db.conversation(corps.conversation_id) is not None:
            await db.ajouter_message(
                corps.conversation_id, "user", resultat.pour_le_modele(),
                kind="outil", tool=resultat.to_dict(),
            )

    return {"proposition": proposition.to_dict(), "resultat": resultat.to_dict()}
