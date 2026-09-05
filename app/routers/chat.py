"""API de conversation native de Larbinus.

`POST /api/chat` renvoie soit un flux SSE (`stream: true`, défaut), soit une
réponse JSON complète. Le flux porte trois types d'événements : `delta`,
`done` et `error`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.schemas import ChatRequest
from app.security import require_api_key
from app.streaming import SSE_HEADERS, sse_event

logger = logging.getLogger("larbinus.chat")

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_api_key)])


@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """Conversation avec le modèle demandé.

    Le champ `model` prend la forme `fournisseur/modèle` (`ollama/mistral`).
    Sans préfixe, `DEFAULT_PROVIDER` s'applique — ou l'unique fournisseur
    configuré s'il n'y en a qu'un.
    """
    registry: ProviderRegistry = request.app.state.registry
    provider = registry.resolve(body.model)

    if not body.stream:
        return await _complete(provider, body)

    return StreamingResponse(
        _event_stream(provider, body, request),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _complete(provider, body: ChatRequest) -> JSONResponse:
    """Mode non-streaming : on consomme le flux et on renvoie le tout d'un bloc."""
    started = time.perf_counter()
    parts: list[str] = []
    finish_reason, usage = "stop", {}

    async for chunk in provider.stream_chat(body):
        parts.append(chunk.delta)
        if chunk.done:
            finish_reason = chunk.finish_reason or "stop"
            usage = chunk.usage

    return JSONResponse(
        {
            "model": body.model,
            "provider": provider.name,
            "content": "".join(parts),
            "finish_reason": finish_reason,
            "usage": usage,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    )


async def _event_stream(provider, body: ChatRequest, request: Request) -> AsyncIterator[str]:
    """Flux SSE. Une erreur de fournisseur devient un événement `error`.

    Les en-têtes sont déjà partis quand elle survient : impossible de renvoyer
    un code HTTP d'erreur, il faut donc l'annoncer dans le flux lui-même.
    """
    started = time.perf_counter()
    try:
        async for chunk in provider.stream_chat(body):
            if await request.is_disconnected():
                logger.info("Client déconnecté, arrêt du flux (%s)", body.model)
                return
            if chunk.delta:
                yield sse_event({"delta": chunk.delta}, event="delta")
            if chunk.done:
                yield sse_event(
                    {
                        "model": body.model,
                        "provider": provider.name,
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
        raise
