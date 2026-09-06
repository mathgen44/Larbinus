"""Personas — les « larbins » : des assistants préconfigurés.

Un persona porte un prompt système, éventuellement un modèle et une température.
Créer une conversation à partir d'un persona **copie** ces réglages dans la
conversation : modifier le persona plus tard ne réécrit donc pas les
conversations passées, dont les réponses avaient été produites avec l'ancienne
consigne.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.security import require_api_key_ui
from app.storage.db import Database

router = APIRouter(
    prefix="/api/personas",
    tags=["personas"],
    dependencies=[Depends(require_api_key_ui)],
)


class PersonaCreation(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    system: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    icon: str | None = Field(default=None, max_length=8)
    tools: list[str] | None = Field(default=None, description="Outils activés.")


class PersonaModification(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    system: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    icon: str | None = Field(default=None, max_length=8)
    tools: list[str] | None = Field(default=None, description="Outils activés.")


def base(request: Request) -> Database:
    return request.app.state.db


async def _ou_404(db: Database, identifiant: str) -> dict:
    persona = await db.persona(identifiant)
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona introuvable.")
    return persona


@router.get("")
async def lister(request: Request) -> list[dict]:
    return await base(request).liste_personas()


@router.post("", status_code=201)
async def creer(request: Request, corps: PersonaCreation) -> dict:
    champs = corps.model_dump()
    champs["tools"] = json.dumps(champs["tools"]) if champs["tools"] is not None else None
    return await base(request).creer_persona(**champs)


@router.get("/{identifiant}")
async def detail(request: Request, identifiant: str) -> dict:
    return await _ou_404(base(request), identifiant)


@router.patch("/{identifiant}")
async def modifier(
    request: Request, identifiant: str, corps: PersonaModification
) -> dict:
    db = base(request)
    await _ou_404(db, identifiant)
    champs = corps.model_dump()
    champs["tools"] = json.dumps(champs["tools"]) if champs["tools"] is not None else None
    return await db.modifier_persona(identifiant, **champs)


@router.delete("/{identifiant}", status_code=204, response_class=Response)
async def supprimer(request: Request, identifiant: str):
    db = base(request)
    await _ou_404(db, identifiant)
    await db.supprimer_persona(identifiant)
    return Response(status_code=204)
