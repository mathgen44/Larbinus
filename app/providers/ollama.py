"""Fournisseur Ollama (instance locale ou sur le LAN).

API native d'Ollama : `/api/tags` pour la liste des modèles, `/api/chat` pour la
conversation, avec un flux NDJSON (un objet JSON complet par ligne).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import ChatChunk, ChatProvider
from app.schemas import ChatRequest, ModelInfo


class OllamaProvider(ChatProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 120.0):
        super().__init__(base_url=base_url, api_key=None, timeout=timeout)

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get(f"{self.base_url}/api/tags")
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        if response.status_code != 200:
            self._raise_http_error(response.status_code, response.text)

        models: list[ModelInfo] = []
        for entry in response.json().get("models", []):
            native = entry.get("name") or entry.get("model")
            if not native:
                continue
            details = entry.get("details") or {}
            models.append(
                ModelInfo(
                    id=f"{self.name}/{native}",
                    name=native,
                    provider=self.name,
                    context_length=details.get("context_length"),
                )
            )
        return models

    async def _stream(self, request: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        system, history = self.split_system(request)
        messages = [{"role": m.role, "content": m.content} for m in history]
        if system:
            messages.insert(0, {"role": "system", "content": system})

        options: dict = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens

        payload = {"model": model, "messages": messages, "stream": True}
        if options:
            payload["options"] = options

        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._raise_http_error(response.status_code, body)

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("error"):
                        self._raise_http_error(400, str(event["error"]))

                    if event.get("done"):
                        yield ChatChunk(
                            done=True,
                            finish_reason=event.get("done_reason") or "stop",
                            usage={
                                "prompt_tokens": event.get("prompt_eval_count", 0),
                                "completion_tokens": event.get("eval_count", 0),
                            },
                        )
                        return

                    delta = (event.get("message") or {}).get("content", "")
                    if delta:
                        yield ChatChunk(delta=delta)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
