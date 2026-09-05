"""Tests de l'API de chat native et de la couche compatible OpenAI (phase 3)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.providers.registry import ProviderRegistry
from tests.conftest import ndjson, patch_provider


def registry_ollama(content: bytes | None = None) -> ProviderRegistry:
    """Registre avec un seul Ollama simulé, qui répond « Bonjour »."""
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    )
    flux = content or ndjson(
        {"message": {"content": "Bon"}, "done": False},
        {"message": {"content": "jour"}, "done": False},
        {"done": True, "prompt_eval_count": 4, "eval_count": 2},
    )
    patch_provider(
        registry.get("ollama"),
        {
            "/api/chat": httpx.Response(200, content=flux),
            "/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]}),
        },
    )
    return registry


def parse_sse(text: str) -> list[tuple[str | None, str]]:
    """Découpe un flux SSE en couples (nom d'événement, données)."""
    events = []
    for block in text.strip().split("\n\n"):
        name, data = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.append((name, "\n".join(data)))
    return events


# --------------------------------------------------------------------------- #
#  API native
# --------------------------------------------------------------------------- #
def test_chat_streaming_sse():
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/api/chat",
            json={"model": "ollama/mistral", "messages": [{"role": "user", "content": "Salut"}]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Sans cet en-tête, un reverse proxy Nginx met le flux en tampon.
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_sse(response.text)
    deltas = [json.loads(d)["delta"] for name, d in events if name == "delta"]
    assert "".join(deltas) == "Bonjour"

    done = [json.loads(d) for name, d in events if name == "done"]
    assert len(done) == 1
    assert done[0]["provider"] == "ollama"
    assert done[0]["usage"]["completion_tokens"] == 2
    assert done[0]["duration_ms"] >= 0


def test_chat_non_streaming():
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/api/chat",
            json={
                "model": "ollama/mistral",
                "messages": [{"role": "user", "content": "Salut"}],
                "stream": False,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Bonjour"
    assert body["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 4, "completion_tokens": 2}


def test_chat_erreur_fournisseur_annoncee_dans_le_flux():
    """Les en-têtes sont déjà partis : l'erreur doit passer par un événement SSE."""
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    )
    patch_provider(
        registry.get("ollama"), {"/api/chat": httpx.Response(500, text="modèle planté")}
    )

    with TestClient(app) as client:
        app.state.registry = registry
        response = client.post(
            "/api/chat",
            json={"model": "ollama/mistral", "messages": [{"role": "user", "content": "Salut"}]},
        )

    assert response.status_code == 200
    events = parse_sse(response.text)
    erreurs = [json.loads(d) for name, d in events if name == "error"]
    assert len(erreurs) == 1
    assert erreurs[0]["error"]["provider"] == "ollama"


def test_modele_sans_fournisseur_resolvable_donne_400():
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://o.test:11434", openai_api_key="k")
    )
    with TestClient(app) as client:
        app.state.registry = registry
        response = client.post(
            "/api/chat",
            json={"model": "ambigu", "messages": [{"role": "user", "content": "Salut"}]},
        )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "ProviderNotConfigured"


# --------------------------------------------------------------------------- #
#  Compatibilité OpenAI
# --------------------------------------------------------------------------- #
def test_openai_chat_completions_non_streaming():
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/v1/chat/completions",
            json={"model": "ollama/mistral", "messages": [{"role": "user", "content": "Salut"}]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Bonjour"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 6


def test_openai_chat_completions_streaming():
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "ollama/mistral",
                "messages": [{"role": "user", "content": "Salut"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    payloads = [d for _, d in parse_sse(response.text)]

    assert payloads[-1] == "[DONE]"
    chunks = [json.loads(p) for p in payloads[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # Premier fragment : le rôle, comme le fait l'API OpenAI.
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    contenu = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert contenu == "Bonjour"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_openai_models_format_liste():
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.get("/v1/models")
    body = response.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "ollama/mistral"
    assert body["data"][0]["owned_by"] == "ollama"


def test_champs_openai_inconnus_ignores():
    """Un client OpenAI envoie souvent des champs que Larbinus ne gère pas."""
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "ollama/mistral",
                "messages": [{"role": "user", "content": "Salut"}],
                "top_p": 0.9,
                "presence_penalty": 0.2,
                "user": "n8n",
            },
        )
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
#  Authentification
# --------------------------------------------------------------------------- #
@pytest.fixture
def cle_activee():
    """Active LARBINUS_API_KEY le temps du test."""
    get_settings.cache_clear()
    original = Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    original.larbinus_api_key = "secret"
    get_settings.cache_clear()
    app.dependency_overrides.clear()

    import app.security as security

    security.get_settings = lambda: original  # type: ignore[assignment]
    yield
    import importlib

    importlib.reload(security)
    get_settings.cache_clear()


def test_cle_exigee_quand_configuree(cle_activee):
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        assert client.get("/api/models").status_code == 401
        assert (
            client.get("/api/models", headers={"X-API-Key": "mauvaise"}).status_code == 401
        )
        assert client.get("/api/models", headers={"X-API-Key": "secret"}).status_code == 200
        # Les clients OpenAI envoient un Bearer : il doit être accepté aussi.
        assert (
            client.get(
                "/v1/models", headers={"Authorization": "Bearer secret"}
            ).status_code
            == 200
        )


def test_health_reste_public(cle_activee):
    """La sonde Docker ne doit jamais dépendre de la clé d'API."""
    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        assert client.get("/health").status_code == 200
