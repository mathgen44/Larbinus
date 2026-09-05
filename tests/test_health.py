"""Tests de fumée du socle (phase 1)."""

import os

os.environ.setdefault("DATA_DIR", "./data")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_health_ok():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert isinstance(body["providers"], list)


def test_root_ok():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "Larbinus"


def test_provider_disabled_without_config(monkeypatch):
    """Un fournisseur sans clé ne doit jamais apparaître comme actif."""
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.enabled_providers == []
