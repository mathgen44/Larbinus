"""API de conversation native de Larbinus.

`POST /api/chat` renvoie soit un flux SSE (`stream: true`, défaut), soit une
réponse JSON complète. Le flux porte les événements `delta` (réponse visible),
`reasoning` (monologue interne), `sources` (extraits de documents), `outils`
(actions proposées), `outil` (résultat d'une exécution), `done` et `error`.

Deux modes de conversation coexistent :

* **sans `conversation_id`** — le client envoie tout l'historique à chaque tour,
  rien n'est enregistré. C'est le mode d'un script ou d'un appel ponctuel.
* **avec `conversation_id`** — la base est la source de vérité : le serveur
  relit l'historique enregistré, y ajoute le nouveau message, puis enregistre
  la question et la réponse.

Quand des outils sont activés, un tour de conversation peut en enchaîner
plusieurs : le modèle propose une action, Larbinus l'exécute si elle est de
consultation, lui transmet le résultat, et il poursuit. Les actions qui
modifient quelque chose interrompent la boucle et attendent l'accord explicite
de l'utilisateur.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.outils.analyse import retirer_blocs
from app.outils.base import Proposition
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.rag.depot import IndexIncoherent
from app.rag.embeddings import EmbeddingIndisponible
from app.schemas import ChatMessage, ChatRequest
from app.security import require_api_key_ui
from app.storage.db import Database
from app.streaming import SSE_HEADERS, sse_event

logger = logging.getLogger("larbinus.chat")

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_api_key_ui)])


@dataclass
class Contexte:
    """Tout ce qui a été décidé avant d'interroger le modèle."""

    requete: ChatRequest
    sources: list[dict]
    outils_actifs: list[str]


def _outils_demandes(body: ChatRequest, conversation: dict | None) -> list[str]:
    """La requête tranche ; sinon on suit le réglage de la conversation."""
    if body.tools is not None:
        return body.tools
    if conversation and conversation.get("tools"):
        try:
            return json.loads(conversation["tools"])
        except json.JSONDecodeError:
            logger.warning("Liste d'outils illisible sur %s", conversation["id"])
    return []


async def _preparer(request: Request, body: ChatRequest) -> Contexte:
    """Reconstitue la requête : historique, prompt système, documents, outils."""
    conversation = None
    messages = list(body.messages)
    systeme = body.system
    temperature = body.temperature

    if body.conversation_id:
        db: Database = request.app.state.db
        conversation = await db.conversation(body.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")

        historique = await db.historique(body.conversation_id)
        messages = [ChatMessage(**m) for m in historique] + messages
        systeme = systeme or conversation.get("system")
        if temperature is None:
            temperature = conversation.get("temperature")

    sources: list[dict] = []
    if body.rag if body.rag is not None else bool(conversation and conversation.get("rag")):
        sources, contexte = await _chercher_documents(request, body, messages)
        if contexte:
            # Placé dans le prompt système et non dans un message : cela vaut
            # pour ce tour, et n'a pas à polluer l'historique enregistré.
            systeme = f"{systeme}\n\n{contexte}" if systeme else contexte

    registre = request.app.state.outils
    actifs = registre.actifs(_outils_demandes(body, conversation))
    if actifs:
        consigne = registre.consigne(actifs)
        systeme = f"{systeme}\n\n{consigne}" if systeme else consigne

    return Contexte(
        requete=body.model_copy(
            update={"messages": messages, "system": systeme, "temperature": temperature}
        ),
        sources=sources,
        outils_actifs=actifs,
    )


async def _chercher_documents(
    request: Request, body: ChatRequest, messages: list[ChatMessage]
) -> tuple[list[dict], str]:
    rag = request.app.state.rag
    if not rag.disponible:
        return [], ""

    question = next((m.content for m in reversed(messages) if m.role == "user"), "")
    try:
        contexte, sources = await rag.contexte(question, limite=body.rag_top_k)
    except (EmbeddingIndisponible, IndexIncoherent) as exc:
        # Un index en panne ne doit pas empêcher de converser.
        logger.warning("Recherche documentaire indisponible : %s", exc)
        return [], ""
    return sources, contexte


async def _enregistrer_question(request: Request, body: ChatRequest) -> None:
    if not body.conversation_id:
        return
    db: Database = request.app.state.db
    for message in body.messages:
        if message.role == "user":
            await db.ajouter_message(body.conversation_id, "user", message.content)


async def _enregistrer_reponse(
    request: Request, body: ChatRequest, provider, contenu: str, raisonnement: str,
    usage: dict, duree_ms: int, sources: list[dict], propositions: list[Proposition],
) -> None:
    if not body.conversation_id or not (contenu or raisonnement or propositions):
        return
    db: Database = request.app.state.db
    await db.ajouter_message(
        body.conversation_id, "assistant", contenu,
        reasoning=raisonnement or None,
        model=body.model, provider=provider.name,
        usage=usage, duration_ms=duree_ms, sources=sources or None,
        tool={"propositions": [p.to_dict() for p in propositions]} if propositions else None,
    )
    await db.modifier_conversation(body.conversation_id, model=body.model)


async def _enregistrer_outil(request: Request, body: ChatRequest, resultat) -> None:
    if not body.conversation_id:
        return
    db: Database = request.app.state.db
    await db.ajouter_message(
        body.conversation_id, "user", resultat.pour_le_modele(),
        kind="outil", tool=resultat.to_dict(),
    )


@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """Conversation avec le modèle demandé.

    Le champ `model` prend la forme `fournisseur/modèle` (`ollama/mistral`).
    """
    registry: ProviderRegistry = request.app.state.registry
    provider = registry.resolve(body.model)

    contexte = await _preparer(request, body)
    await _enregistrer_question(request, body)

    if not body.stream:
        return await _complete(request, body, contexte, provider)

    return StreamingResponse(
        _event_stream(request, body, contexte, provider),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _un_tour(provider, requete: ChatRequest, messages: list[ChatMessage]):
    """Un aller-retour avec le modèle, sans streaming."""
    parts: list[str] = []
    raisonnement: list[str] = []
    usage: dict = {}
    finish = "stop"

    async for chunk in provider.stream_chat(requete.model_copy(update={"messages": messages})):
        parts.append(chunk.delta)
        raisonnement.append(chunk.reasoning)
        if chunk.done:
            usage = chunk.usage
            finish = chunk.finish_reason or "stop"
    return "".join(parts), "".join(raisonnement), usage, finish


async def _complete(
    request: Request, body: ChatRequest, contexte: Contexte, provider
) -> JSONResponse:
    """Mode non-streaming, boucle d'outils comprise."""
    registre = request.app.state.outils
    started = time.perf_counter()
    messages = list(contexte.requete.messages)

    contenu = raisonnement = ""
    usage: dict = {}
    finish = "stop"
    executions: list[dict] = []
    en_attente: list[dict] = []

    for iteration in range(request.app.state.settings.tool_max_iterations + 1):
        contenu, raisonnement, usage, finish = await _un_tour(
            provider, contexte.requete, messages
        )
        propositions = registre.propositions(contenu, contexte.outils_actifs)
        propre = retirer_blocs(contenu) if propositions else contenu

        await _enregistrer_reponse(
            request, body, provider, propre, raisonnement, usage,
            round((time.perf_counter() - started) * 1000),
            contexte.sources if iteration == 0 else [], propositions,
        )
        contenu = propre
        if not propositions:
            break

        automatiques = [p for p in propositions if registre.automatique(p)]
        en_attente = [p.to_dict() for p in propositions if not registre.automatique(p)]
        if not automatiques or iteration == request.app.state.settings.tool_max_iterations:
            break

        messages = messages + [ChatMessage(role="assistant", content=propre)]
        for proposition in automatiques:
            resultat = await registre.executer(proposition)
            executions.append(resultat.to_dict())
            await _enregistrer_outil(request, body, resultat)
            messages.append(ChatMessage(role="user", content=resultat.pour_le_modele()))

    return JSONResponse(
        {
            "model": body.model,
            "provider": provider.name,
            "conversation_id": body.conversation_id,
            "content": contenu,
            **({"reasoning": raisonnement} if raisonnement else {}),
            **({"sources": contexte.sources} if contexte.sources else {}),
            **({"outils": executions} if executions else {}),
            **({"confirmations": en_attente} if en_attente else {}),
            "finish_reason": finish,
            "usage": usage,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    )


async def _event_stream(
    request: Request, body: ChatRequest, contexte: Contexte, provider
) -> AsyncIterator[str]:
    """Flux SSE, boucle d'outils comprise.

    Une erreur survenue après le début du flux devient un événement `error` :
    les en-têtes sont déjà partis, impossible de renvoyer un code HTTP.
    """
    registre = request.app.state.outils
    plafond = request.app.state.settings.tool_max_iterations
    started = time.perf_counter()
    messages = list(contexte.requete.messages)

    parts: list[str] = []
    raisonnement: list[str] = []
    usage: dict = {}
    interrompu = False
    propositions: list[Proposition] = []

    try:
        if contexte.sources:
            yield sse_event({"sources": contexte.sources}, event="sources")

        for iteration in range(plafond + 1):
            tour: list[str] = []
            tour_raisonnement: list[str] = []

            async for chunk in provider.stream_chat(
                contexte.requete.model_copy(update={"messages": messages})
            ):
                if await request.is_disconnected():
                    logger.info("Client déconnecté, arrêt du flux (%s)", body.model)
                    interrompu = True
                    break
                if chunk.reasoning:
                    tour_raisonnement.append(chunk.reasoning)
                    yield sse_event({"reasoning": chunk.reasoning}, event="reasoning")
                if chunk.delta:
                    tour.append(chunk.delta)
                    yield sse_event({"delta": chunk.delta}, event="delta")
                if chunk.done:
                    usage = chunk.usage

            texte = "".join(tour)
            raisonnement.append("".join(tour_raisonnement))
            if interrompu:
                parts.append(texte)
                break

            propositions = registre.propositions(texte, contexte.outils_actifs)
            propre = retirer_blocs(texte) if propositions else texte
            parts.append(propre)

            await _enregistrer_reponse(
                request, body, provider, propre, "".join(tour_raisonnement), usage,
                round((time.perf_counter() - started) * 1000),
                contexte.sources if iteration == 0 else [], propositions,
            )

            if not propositions:
                break

            yield sse_event(
                {"propositions": [p.to_dict() for p in propositions]}, event="outils"
            )

            automatiques = [p for p in propositions if registre.automatique(p)]
            if not automatiques:
                # Rien ne part seul : la main revient à l'utilisateur.
                break
            if iteration == plafond:
                yield sse_event(
                    {
                        "message": f"Plafond de {plafond} enchaînements atteint : "
                                   "les actions suivantes attendent votre accord.",
                    },
                    event="plafond",
                )
                break

            messages = messages + [ChatMessage(role="assistant", content=propre)]
            for proposition in automatiques:
                resultat = await registre.executer(proposition)
                yield sse_event({"resultat": resultat.to_dict()}, event="outil")
                await _enregistrer_outil(request, body, resultat)
                messages.append(
                    ChatMessage(role="user", content=resultat.pour_le_modele())
                )

        yield sse_event(
            {
                "model": body.model,
                "provider": provider.name,
                "conversation_id": body.conversation_id,
                "finish_reason": "stop",
                "usage": usage,
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
        if interrompu and body.conversation_id and parts:
            # Une génération interrompue reste utile : on garde ce qui a été
            # reçu plutôt que de perdre le tour.
            await _enregistrer_reponse(
                request, body, provider, parts[-1], raisonnement[-1] if raisonnement else "",
                usage, round((time.perf_counter() - started) * 1000), [], [],
            )
