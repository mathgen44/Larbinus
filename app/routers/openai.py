"""Couche compatible OpenAI.

Expose `/v1/chat/completions` et `/v1/models` au format attendu par les clients
OpenAI : n8n, Open WebUI, les SDK officiels et tout ce qui parle ce dialecte
peuvent viser Larbinus sans adaptateur.

Le champ `model` reste l'identifiant Larbinus (`ollama/mistral`), ce qui permet
de choisir le fournisseur depuis un client qui ne connaît qu'OpenAI.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest
from app.security import require_api_key
from app.streaming import SSE_HEADERS, sse_event

logger = logging.getLogger("larbinus.openai")

router = APIRouter(prefix="/v1", tags=["compatible OpenAI"], dependencies=[Depends(require_api_key)])


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class OpenAIChatRequest(BaseModel):
    """Sous-ensemble utile du contrat OpenAI ; les champs inconnus sont ignorés."""

    model: str
    messages: list[OpenAIMessage] = Field(..., min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = False

    model_config = {"extra": "ignore"}

    def to_internal(self) -> ChatRequest:
        return ChatRequest(
            model=self.model,
            messages=[ChatMessage(role=m.role, content=m.content) for m in self.messages],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=self.stream,
        )


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


@router.get("/models")
async def openai_models(request: Request) -> JSONResponse:
    """Liste des modèles au format OpenAI (`{"object": "list", "data": [...]}`)."""
    registry: ProviderRegistry = request.app.state.registry
    models = await registry.list_models()
    created = int(time.time())
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": m.id,
                    "object": "model",
                    "created": created,
                    "owned_by": m.provider,
                }
                for m in models
            ],
        }
    )


@router.post("/chat/completions")
async def openai_chat_completions(request: Request, body: OpenAIChatRequest):
    registry: ProviderRegistry = request.app.state.registry
    internal = body.to_internal()
    provider = registry.resolve(internal.model)

    if body.stream:
        return StreamingResponse(
            _stream_openai(provider, internal, request),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    return await _complete_openai(provider, internal)


async def _complete_openai(provider, internal: ChatRequest) -> JSONResponse:
    parts: list[str] = []
    finish_reason, usage = "stop", {}

    async for chunk in provider.stream_chat(internal):
        parts.append(chunk.delta)
        if chunk.done:
            finish_reason = chunk.finish_reason or "stop"
            usage = chunk.usage

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return JSONResponse(
        {
            "id": _completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": internal.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(parts)},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


async def _stream_openai(provider, internal: ChatRequest, request: Request) -> AsyncIterator[str]:
    """Flux au format `chat.completion.chunk`, terminé par `data: [DONE]`."""
    completion_id = _completion_id()
    created = int(time.time())

    def envelope(delta: dict, finish_reason: str | None = None) -> dict:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": internal.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    try:
        # Premier fragment : le rôle, comme le fait l'API OpenAI. Certains clients
        # (dont d'anciens nœuds n8n) s'attendent à le recevoir avant tout contenu.
        yield sse_event(envelope({"role": "assistant"}))

        async for chunk in provider.stream_chat(internal):
            if await request.is_disconnected():
                logger.info("Client déconnecté, arrêt du flux (%s)", internal.model)
                return
            if chunk.delta:
                yield sse_event(envelope({"content": chunk.delta}))
            if chunk.done:
                yield sse_event(envelope({}, finish_reason=chunk.finish_reason or "stop"))
    except ProviderError as exc:
        logger.warning("Erreur pendant le flux OpenAI : %s", exc)
        yield sse_event(exc.to_dict())

    yield sse_event("[DONE]")
