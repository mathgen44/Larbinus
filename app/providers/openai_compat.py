"""Fournisseur OpenAI et toute API qui en reprend le contrat.

Une seule implémentation couvre OpenAI, OpenRouter, Groq, vLLM, LM Studio et
Mistral AI : seules l'URL de base, la clé et le nom changent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import ChatChunk, ChatProvider
from app.schemas import ChatRequest, ModelInfo


class OpenAICompatibleProvider(ChatProvider):
    name = "openai"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get(
                f"{self.base_url}/models", headers=self.headers
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
                ModelInfo(
                    id=f"{self.name}/{native}",
                    name=native,
                    provider=self.name,
                    context_length=entry.get("context_length")
                    or entry.get("max_context_length"),
                )
            )
        return models

    def _build_payload(self, request: ChatRequest, model: str) -> dict:
        system, history = self.split_system(request)
        messages = [{"role": m.role, "content": m.content} for m in history]
        if system:
            messages.insert(0, {"role": "system", "content": system})

        payload: dict = {"model": model, "messages": messages, "stream": True}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    async def _stream(self, request: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        payload = self._build_payload(request, model)
        usage: dict[str, int] = {}
        finish_reason: str | None = None

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self.headers,
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._raise_http_error(response.status_code, body)

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if event.get("usage"):
                        usage = {
                            "prompt_tokens": event["usage"].get("prompt_tokens", 0),
                            "completion_tokens": event["usage"].get("completion_tokens", 0),
                        }

                    for choice in event.get("choices", []):
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield ChatChunk(delta=delta)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc

        yield ChatChunk(done=True, finish_reason=finish_reason or "stop", usage=usage)


class MistralProvider(OpenAICompatibleProvider):
    """Mistral AI expose une API compatible OpenAI : seul le nom change."""

    name = "mistral"
