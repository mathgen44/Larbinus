"""Fournisseur Anthropic (Claude).

Contrat différent d'OpenAI : endpoint `/v1/messages`, prompt système dans un champ
dédié `system` (et non un message de rôle `system`), `max_tokens` obligatoire, et
un flux SSE typé par événement.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import ChatChunk, ChatProvider
from app.schemas import ChatRequest, ModelInfo

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(ChatProvider):
    name = "anthropic"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/models", headers=self.headers
            )
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        if response.status_code != 200:
            self._raise_http_error(response.status_code, response.text)

        models: list[ModelInfo] = []
        for entry in response.json().get("data", []):
            native = entry.get("id")
            if not native:
                continue
            models.append(
                ModelInfo(id=f"{self.name}/{native}", name=native, provider=self.name)
            )
        return models

    async def _stream(self, request: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        system, history = self.split_system(request)
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in history],
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        usage: dict[str, int] = {}
        finish_reason: str | None = None

        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/v1/messages", json=payload, headers=self.headers
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._raise_http_error(response.status_code, body)

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    kind = event.get("type")

                    if kind == "message_start":
                        started = (event.get("message") or {}).get("usage") or {}
                        usage["prompt_tokens"] = started.get("input_tokens", 0)

                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield ChatChunk(delta=delta["text"])

                    elif kind == "message_delta":
                        finish_reason = (event.get("delta") or {}).get("stop_reason")
                        usage["completion_tokens"] = (event.get("usage") or {}).get(
                            "output_tokens", 0
                        )

                    elif kind == "error":
                        message = (event.get("error") or {}).get("message", "erreur inconnue")
                        self._raise_http_error(400, message)

                    elif kind == "message_stop":
                        break
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc

        yield ChatChunk(done=True, finish_reason=finish_reason or "stop", usage=usage)
