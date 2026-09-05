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


def test_root_sert_l_interface():
    """La racine sert la page de chat (phase 4), pas du JSON."""
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Larbinus</title>" in response.text
    # Une image à jour ne doit pas rester masquée par le cache du navigateur.
    assert response.headers["cache-control"] == "no-cache"


def test_fichiers_statiques_servis():
    with TestClient(app) as client:
        for chemin, type_attendu in [
            ("/static/app.js", "javascript"),
            ("/static/styles.css", "text/css"),
        ]:
            reponse = client.get(chemin)
            assert reponse.status_code == 200, chemin
            assert type_attendu in reponse.headers["content-type"]


def test_provider_disabled_without_config(monkeypatch):
    """Un fournisseur sans clé ne doit jamais apparaître comme actif."""
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.enabled_providers == []
