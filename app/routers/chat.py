"""API de conversation native de Larbinus.

`POST /api/chat` renvoie soit un flux SSE (`stream: true`, défaut), soit une
réponse JSON complète. Le flux porte quatre types d'événements : `delta`
(réponse visible), `reasoning` (monologue interne des modèles de raisonnement),
`done` et `error`.

Deux modes de conversation coexistent :

* **sans `conversation_id`** — le client envoie tout l'historique à chaque tour,
  rien n'est enregistré. C'est le mode d'un script ou d'un appel ponctuel.
* **avec `conversation_id`** — la base est la source de vérité : le serveur
  relit l'historique enregistré, y ajoute le nouveau message, puis enregistre
  la question et la réponse. Le client n'envoie plus que le message du tour, ce
  qui interdit toute divergence entre son état et celui du serveur.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest
from app.security import require_api_key
from app.storage.db import Database
from app.streaming import SSE_HEADERS, sse_event

logger = logging.getLogger("larbinus.chat")

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_api_key)])


async def _preparer(request: Request, body: ChatRequest) -> ChatRequest:
    """Reconstitue la requête complète à partir de l'historique enregistré."""
    if not body.conversation_id:
        return body

    db: Database = request.app.state.db
    conversation = await db.conversation(body.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    historique = await db.historique(body.conversation_id)
    complet = body.model_copy(
        update={
            "messages": [ChatMessage(**m) for m in historique] + list(body.messages),
            # Le prompt système et la température de la conversation s'appliquent
            # si la requête n'en fournit pas pour ce tour précis — c'est ainsi
            # qu'un persona continue d'agir sans que le client ait à le renvoyer.
            "system": body.system or conversation.get("system"),
            "temperature": body.temperature
            if body.temperature is not None
            else conversation.get("temperature"),
        }
    )
    return complet


async def _enregistrer_question(request: Request, body: ChatRequest) -> None:
    if not body.conversation_id:
        return
    db: Database = request.app.state.db
    for message in body.messages:
        if message.role == "user":
            await db.ajouter_message(body.conversation_id, "user", message.content)


async def _enregistrer_reponse(
    request: Request,
    body: ChatRequest,
    provider,
    contenu: str,
    raisonnement: str,
    usage: dict,
    duree_ms: int,
) -> None:
    if not body.conversation_id or not (contenu or raisonnement):
        return
    db: Database = request.app.state.db
    await db.ajouter_message(
        body.conversation_id,
        "assistant",
        contenu,
        reasoning=raisonnement or None,
        model=body.model,
        provider=provider.name,
        usage=usage,
        duration_ms=duree_ms,
    )
    await db.modifier_conversation(body.conversation_id, model=body.model)


@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """Conversation avec le modèle demandé.

    Le champ `model` prend la forme `fournisseur/modèle` (`ollama/mistral`).
    Sans préfixe, `DEFAULT_PROVIDER` s'applique — ou l'unique fournisseur
    configuré s'il n'y en a qu'un.
    """
    registry: ProviderRegistry = request.app.state.registry
    provider = registry.resolve(body.model)

    complet = await _preparer(request, body)
    await _enregistrer_question(request, body)

    if not body.stream:
        return await _complete(request, body, complet, provider)

    return StreamingResponse(
        _event_stream(request, body, complet, provider),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _complete(
    request: Request, body: ChatRequest, complet: ChatRequest, provider
) -> JSONResponse:
    """Mode non-streaming : on consomme le flux et on renvoie le tout d'un bloc."""
    started = time.perf_counter()
    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason, usage = "stop", {}

    async for chunk in provider.stream_chat(complet):
        parts.append(chunk.delta)
        reasoning_parts.append(chunk.reasoning)
        if chunk.done:
            finish_reason = chunk.finish_reason or "stop"
            usage = chunk.usage

    contenu = "".join(parts)
    reasoning = "".join(reasoning_parts)
    duree = round((time.perf_counter() - started) * 1000)

    await _enregistrer_reponse(request, body, provider, contenu, reasoning, usage, duree)

    return JSONResponse(
        {
            "model": body.model,
            "provider": provider.name,
            "conversation_id": body.conversation_id,
            "content": contenu,
            # Absent pour un modèle classique, plutôt qu'une chaîne vide :
            # le client sait ainsi distinguer « pas de raisonnement » de « vide ».
            **({"reasoning": reasoning} if reasoning else {}),
            "finish_reason": finish_reason,
            "usage": usage,
            "duration_ms": duree,
        }
    )


async def _event_stream(
    request: Request, body: ChatRequest, complet: ChatRequest, provider
) -> AsyncIterator[str]:
    """Flux SSE. Une erreur de fournisseur devient un événement `error`.

    Les en-têtes sont déjà partis quand elle survient : impossible de renvoyer
    un code HTTP d'erreur, il faut donc l'annoncer dans le flux lui-même.
    """
    started = time.perf_counter()
    parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict = {}
    interrompu = False

    try:
        async for chunk in provider.stream_chat(complet):
            if await request.is_disconnected():
                logger.info("Client déconnecté, arrêt du flux (%s)", body.model)
                interrompu = True
                break
            if chunk.reasoning:
                reasoning_parts.append(chunk.reasoning)
                yield sse_event({"reasoning": chunk.reasoning}, event="reasoning")
            if chunk.delta:
                parts.append(chunk.delta)
                yield sse_event({"delta": chunk.delta}, event="delta")
            if chunk.done:
                usage = chunk.usage
                yield sse_event(
                    {
                        "model": body.model,
                        "provider": provider.name,
                        "conversation_id": body.conversation_id,
                        "finish_reason": chunk.finish_reason or "stop",
                        "usage": chunk.usage,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    },
                    event="done",
                )
    except ProviderError as exc:
        logger.warning("Erreur pendant le flux : %s", exc)
        yield sse_event(exc.to_dict(), event="error")
    except asyncio.CancelledError:
        logger.info("Flux annulé (%s)", body.model)
        interrompu = True
        raise
    finally:
        # Une génération interrompue reste utile : on garde ce qui a été reçu
        # plutôt que de perdre le tour.
        await _enregistrer_reponse(
            request,
            body,
            provider,
            "".join(parts),
            "".join(reasoning_parts),
            usage,
            round((time.perf_counter() - started) * 1000),
        )
        if interrompu:
            logger.info("Réponse partielle enregistrée (%s)", body.conversation_id)
