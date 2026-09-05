"""Tests des routes /api/models et /api/providers."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.registry import ProviderRegistry
from tests.conftest import patch_provider


def _registry_simule() -> ProviderRegistry:
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    )
    patch_provider(
        registry.get("ollama"),
        {"/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]})},
    )
    return registry


def test_api_models_agrege_les_fournisseurs():
    with TestClient(app) as client:
        app.state.registry = _registry_simule()
        response = client.get("/api/models")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "ollama/mistral",
            "name": "mistral",
            "provider": "ollama",
            "context_length": None,
        }
    ]


def test_api_providers_signale_une_panne():
    with TestClient(app) as client:
        registry = ProviderRegistry(
            Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
        )
        patch_provider(
            registry.get("ollama"), {"/api/tags": httpx.Response(503, text="arrêté")}
        )
        app.state.registry = registry
        response = client.get("/api/providers")

    assert response.status_code == 200
    status = response.json()[0]
    assert status["name"] == "ollama"
    assert status["available"] is False
    assert "503" in status["detail"]
