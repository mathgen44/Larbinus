"""Outillage commun aux tests : faux transport HTTP, sans aucun appel réseau."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings


def sse(*events: dict | str) -> bytes:
    """Construit un flux SSE (`data: ...`) à partir d'objets Python."""
    lines = []
    for event in events:
        payload = event if isinstance(event, str) else json.dumps(event)
        lines.append(f"data: {payload}\n\n")
    return "".join(lines).encode()


def ndjson(*events: dict) -> bytes:
    """Construit un flux NDJSON (une ligne JSON par événement), format d'Ollama."""
    return "".join(json.dumps(e) + "\n" for e in events).encode()


def make_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Associe un chemin d'URL à une réponse fixe."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = routes.get(request.url.path)
        if response is None:
            return httpx.Response(404, text=f"chemin non simulé : {request.url.path}")
        return response

    return httpx.MockTransport(handler)


def patch_provider(provider, routes: dict[str, httpx.Response]):
    """Remplace le client HTTP d'un fournisseur par un transport simulé."""
    provider._client = httpx.AsyncClient(transport=make_transport(routes))
    return provider


@pytest.fixture
def settings_ollama_only() -> Settings:
    return Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
