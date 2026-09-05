"""Gestion des conversations enregistrées."""

from __future__ import annotations

import json
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.security import require_api_key
from app.storage.db import Database

router = APIRouter(
    prefix="/api/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_api_key)],
)


class ConversationCreation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    system: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    persona_id: str | None = Field(
        default=None,
        description="Applique les réglages du persona à la conversation créée. "
        "Ils sont copiés, pas référencés : modifier le persona ensuite ne "
        "réécrit pas les conversations passées.",
    )


class ConversationModification(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    system: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


def base(request: Request) -> Database:
    return request.app.state.db


def nom_de_fichier(titre: str, identifiant: str, extension: str) -> str:
    """Nom lisible dérivé du titre — un UUID ne dit rien dans un dossier."""
    sans_accent = unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode()
    limace = re.sub(r"[^a-zA-Z0-9]+", "-", sans_accent).strip("-").lower()[:60]
    return f"{limace or 'conversation'}-{identifiant[:8]}.{extension}"


async def _ou_404(db: Database, identifiant: str) -> dict:
    conversation = await db.conversation(identifiant)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return conversation


@router.get("")
async def lister(request: Request) -> list[dict]:
    """Conversations, de la plus récemment modifiée à la plus ancienne."""
    return await base(request).liste_conversations()


@router.post("", status_code=201)
async def creer(request: Request, corps: ConversationCreation) -> dict:
    db = base(request)

    titre, modele, systeme, temperature = (
        corps.title, corps.model, corps.system, corps.temperature
    )
    if corps.persona_id:
        persona = await db.persona(corps.persona_id)
        if persona is None:
            raise HTTPException(status_code=404, detail="Persona introuvable.")
        # Ce que la requête précise l'emporte ; le persona ne fournit que
        # les valeurs manquantes. Le titre reste volontairement celui par
        # défaut : il sera remplacé par la première question, bien plus utile
        # dans la liste que quatre conversations nommées « Développeur ».
        modele = modele or persona["model"]
        systeme = systeme or persona["system"]
        temperature = temperature if temperature is not None else persona["temperature"]

    return await db.creer_conversation(
        titre=titre, modele=modele, systeme=systeme,
        persona_id=corps.persona_id, temperature=temperature,
    )


@router.get("/{identifiant}")
async def detail(request: Request, identifiant: str) -> dict:
    db = base(request)
    conversation = await _ou_404(db, identifiant)
    conversation["messages"] = await db.messages(identifiant)
    return conversation


@router.patch("/{identifiant}")
async def modifier(
    request: Request, identifiant: str, corps: ConversationModification
) -> dict:
    db = base(request)
    await _ou_404(db, identifiant)
    return await db.modifier_conversation(
        identifiant, title=corps.title, model=corps.model,
        system=corps.system, temperature=corps.temperature,
    )


@router.delete("/{identifiant}", status_code=204, response_class=Response)
async def supprimer(request: Request, identifiant: str):
    """204 sans corps : FastAPI refuse un modèle de réponse sur ce code."""
    db = base(request)
    await _ou_404(db, identifiant)
    await db.supprimer_conversation(identifiant)
    return Response(status_code=204)


@router.get("/{identifiant}/export", response_class=PlainTextResponse)
async def exporter(request: Request, identifiant: str, format: str = "md"):
    """Export d'une conversation en Markdown (défaut) ou en JSON."""
    db = base(request)
    conversation = await _ou_404(db, identifiant)
    messages = await db.messages(identifiant)

    if format == "json":
        return PlainTextResponse(
            json.dumps({**conversation, "messages": messages}, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=\""
                + nom_de_fichier(conversation["title"], identifiant, "json")
                + "\""
            },
        )

    lignes = [f"# {conversation['title']}", ""]
    if conversation.get("system"):
        lignes += ["> **Prompt système** — " + conversation["system"], ""]

    for message in messages:
        auteur = "Vous" if message["role"] == "user" else "Assistant"
        entete = f"## {auteur}"
        if message["role"] == "assistant" and message.get("model"):
            entete += f" · `{message['model']}`"
        lignes += [entete, ""]
        if message.get("reasoning"):
            # Le raisonnement est conservé mais replié : il alourdit la lecture
            # sans être la réponse.
            lignes += ["<details><summary>Raisonnement</summary>", "",
                       message["reasoning"], "", "</details>", ""]
        lignes += [message["content"], ""]

    return PlainTextResponse(
        "\n".join(lignes),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=\""
            + nom_de_fichier(conversation["title"], identifiant, "md")
            + "\""
        },
    )
