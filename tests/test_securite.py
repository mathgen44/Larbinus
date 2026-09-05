"""Sécurité et exploitation (phase 8) : clé d'API, débit, journal, contexte."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import Settings, get_settings
from app.main import app
from app.middleware import JournalJson, adresse_client
from app.providers.registry import ProviderRegistry
from tests.conftest import patch_provider


@pytest.fixture
def registre_simule():
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    )
    patch_provider(
        registry.get("ollama"),
        {"/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]})},
    )
    return registry


@pytest.fixture
def reglages(tmp_path):
    """Réglages isolés, réinjectés dans le module de sécurité."""
    import app.main as principal
    import app.security as securite

    principal.settings.data_dir = str(tmp_path)
    reglages = Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    securite.get_settings = lambda: reglages
    yield reglages

    import importlib

    importlib.reload(securite)
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
#  Portée de la clé d'API
# --------------------------------------------------------------------------- #
def test_sans_cle_tout_est_ouvert(reglages, registre_simule):
    reglages.larbinus_api_key = None
    with TestClient(app) as client:
        app.state.registry = registre_simule
        assert client.get("/api/models").status_code == 200
        assert client.get("/v1/models").status_code == 200


def test_cle_exigee_sur_v1_mais_pas_sur_l_interface(reglages, registre_simule):
    """Choix assumé : le LAN est de confiance, l'interface reste utilisable."""
    reglages.larbinus_api_key = "secret"
    reglages.larbinus_protect_ui = False

    with TestClient(app) as client:
        app.state.registry = registre_simule
        assert client.get("/api/models").status_code == 200
        assert client.get("/v1/models").status_code == 401
        assert client.get(
            "/v1/models", headers={"Authorization": "Bearer secret"}
        ).status_code == 200


def test_protect_ui_ferme_aussi_l_interface(reglages, registre_simule):
    reglages.larbinus_api_key = "secret"
    reglages.larbinus_protect_ui = True

    with TestClient(app) as client:
        app.state.registry = registre_simule
        assert client.get("/api/models").status_code == 401
        assert client.get(
            "/api/models", headers={"X-API-Key": "secret"}
        ).status_code == 200
        assert client.get("/api/conversations").status_code == 401
        assert client.get("/api/documents").status_code == 401


def test_health_reste_public_meme_protege(reglages, registre_simule):
    """La sonde Docker ne doit jamais dépendre de la clé."""
    reglages.larbinus_api_key = "secret"
    reglages.larbinus_protect_ui = True
    with TestClient(app) as client:
        app.state.registry = registre_simule
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- #
#  Identifiant de requête et journal
# --------------------------------------------------------------------------- #
def test_identifiant_de_requete_present_et_repris(reglages, registre_simule):
    with TestClient(app) as client:
        app.state.registry = registre_simule

        reponse = client.get("/api/models")
        assert reponse.headers["X-Request-ID"]

        # Un identifiant fourni par un reverse proxy doit être conservé, pour
        # pouvoir suivre une requête d'un service à l'autre.
        reponse = client.get("/api/models", headers={"X-Request-ID": "abc123"})
        assert reponse.headers["X-Request-ID"] == "abc123"


def test_un_rejet_429_porte_aussi_un_identifiant(reglages, registre_simule):
    """L'ordre des intergiciels doit placer le contexte au-dessus du débit,
    sinon une requête rejetée repart sans identifiant ni ligne de journal."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.middleware import ContexteRequete, LimitationDebit

    async def bonjour(request):
        return PlainTextResponse("ok")

    application = Starlette(routes=[Route("/api/test", bonjour)])
    # Même ordre d'ajout que dans main.py.
    application.add_middleware(LimitationDebit, limite=1, fenetre=60, proxys=set())
    application.add_middleware(ContexteRequete)

    with TestClient(application) as client:
        assert client.get("/api/test").status_code == 200
        refus = client.get("/api/test")
        assert refus.status_code == 429
        assert refus.headers["X-Request-ID"]


def test_journal_json_serialise_les_champs_utiles():
    enregistrement = logging.LogRecord(
        "larbinus.acces", logging.INFO, "x", 1, "GET /api/models → %s", (200,), None
    )
    enregistrement.request_id = "abc123"
    enregistrement.chemin = "/api/models"
    enregistrement.statut = 200
    enregistrement.duree_ms = 12

    charge = json.loads(JournalJson().format(enregistrement))
    assert charge["niveau"] == "INFO"
    assert charge["source"] == "larbinus.acces"
    assert charge["request_id"] == "abc123"
    assert charge["statut"] == 200
    assert charge["duree_ms"] == 12
    assert "GET /api/models" in charge["message"]


# --------------------------------------------------------------------------- #
#  Adresse réelle du client
# --------------------------------------------------------------------------- #
def _requete(client_host: str, entetes: dict[str, str] | None = None) -> Request:
    brut = [
        (cle.lower().encode(), valeur.encode())
        for cle, valeur in (entetes or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": brut,
            "client": (client_host, 12345),
        }
    )


def test_x_forwarded_for_ignore_si_le_proxy_n_est_pas_declare():
    """Sinon n'importe qui contournerait la limitation en falsifiant l'en-tête."""
    requete = _requete("192.168.0.99", {"X-Forwarded-For": "1.2.3.4"})
    assert adresse_client(requete, set()) == "192.168.0.99"
    assert adresse_client(requete, {"172.18.0.1"}) == "192.168.0.99"


def test_x_forwarded_for_suivi_depuis_un_proxy_declare():
    requete = _requete("172.18.0.1", {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})
    assert adresse_client(requete, {"172.18.0.1"}) == "1.2.3.4"


# --------------------------------------------------------------------------- #
#  Limitation de débit
# --------------------------------------------------------------------------- #
def test_limitation_de_debit(reglages, registre_simule, monkeypatch):
    """Un plafond bas, pour vérifier le rejet et l'en-tête Retry-After."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.middleware import LimitationDebit

    async def bonjour(request):
        return PlainTextResponse("ok")

    application = Starlette(routes=[Route("/api/test", bonjour), Route("/health", bonjour)])
    application.add_middleware(LimitationDebit, limite=3, fenetre=60, proxys=set())

    with TestClient(application) as client:
        for tour in range(3):
            reponse = client.get("/api/test")
            assert reponse.status_code == 200, tour
            assert reponse.headers["X-RateLimit-Limit"] == "3"

        refus = client.get("/api/test")
        assert refus.status_code == 429
        assert refus.json()["error"]["type"] == "DebitDepasse"
        assert int(refus.headers["Retry-After"]) > 0

        # La sonde Docker ne doit jamais être bloquée : sans exemption, le
        # conteneur finirait par se déclarer lui-même en panne.
        assert client.get("/health").status_code == 200


def test_limitation_desactivable():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.middleware import LimitationDebit

    async def bonjour(request):
        return PlainTextResponse("ok")

    application = Starlette(routes=[Route("/api/test", bonjour)])
    application.add_middleware(LimitationDebit, limite=0, fenetre=60, proxys=set())

    with TestClient(application) as client:
        for _ in range(30):
            assert client.get("/api/test").status_code == 200
