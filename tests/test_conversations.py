"""Persistance des conversations (phase 5)."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.registry import ProviderRegistry
from app.storage.db import Database, titre_depuis
from tests.conftest import ndjson, patch_provider
from tests.test_chat import parse_sse

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client de test avec une base neuve et un Ollama simulé."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.main as principal

    principal.settings.data_dir = str(tmp_path)

    with TestClient(app) as testeur:
        registry = ProviderRegistry(
            Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
        )
        patch_provider(
            registry.get("ollama"),
            {
                "/api/chat": httpx.Response(
                    200,
                    content=ndjson(
                        {"message": {"thinking": "je réfléchis"}, "done": False},
                        {"message": {"content": "Bonjour"}, "done": False},
                        {"done": True, "prompt_eval_count": 4, "eval_count": 2},
                    ),
                ),
                "/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]}),
            },
        )
        app.state.registry = registry
        yield testeur


# --------------------------------------------------------------------------- #
#  Base
# --------------------------------------------------------------------------- #
async def test_base_cycle_complet(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()

    conversation = await db.creer_conversation()
    assert conversation["title"] == "Nouvelle conversation"

    await db.ajouter_message(conversation["id"], "user", "Quelle est la capitale ?")
    await db.ajouter_message(
        conversation["id"], "assistant", "Paris.",
        reasoning="je cherche", model="ollama/mistral", provider="ollama",
        usage={"completion_tokens": 2}, duration_ms=120,
    )

    # Le titre par défaut est remplacé par la première question.
    rechargee = await db.conversation(conversation["id"])
    assert rechargee["title"] == "Quelle est la capitale ?"

    messages = await db.messages(conversation["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["usage"] == {"completion_tokens": 2}

    # L'historique renvoyé aux fournisseurs exclut le raisonnement.
    historique = await db.historique(conversation["id"])
    assert historique == [
        {"role": "user", "content": "Quelle est la capitale ?"},
        {"role": "assistant", "content": "Paris."},
    ]

    assert await db.supprimer_conversation(conversation["id"]) is True
    assert await db.conversation(conversation["id"]) is None
    # La suppression en cascade doit emporter les messages.
    assert await db.messages(conversation["id"]) == []
    await db.close()


def test_titre_tronque_proprement():
    assert titre_depuis("  Bonjour   le   monde ") == "Bonjour le monde"
    assert titre_depuis("") == "Nouvelle conversation"
    long = titre_depuis("a" * 200)
    assert len(long) == 60 and long.endswith("…")


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
def test_crud_conversations(client):
    assert client.get("/api/conversations").json() == []

    creation = client.post("/api/conversations", json={"model": "ollama/mistral"})
    assert creation.status_code == 201
    identifiant = creation.json()["id"]

    liste = client.get("/api/conversations").json()
    assert len(liste) == 1 and liste[0]["message_count"] == 0

    renommee = client.patch(f"/api/conversations/{identifiant}", json={"title": "Essai"})
    assert renommee.json()["title"] == "Essai"

    assert client.delete(f"/api/conversations/{identifiant}").status_code == 204
    assert client.get(f"/api/conversations/{identifiant}").status_code == 404
    assert client.get("/api/conversations").json() == []


def test_conversation_inconnue_donne_404(client):
    reponse = client.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": "inexistante",
              "messages": [{"role": "user", "content": "Salut"}], "stream": False},
    )
    assert reponse.status_code == 404


def test_chat_enregistre_question_et_reponse(client):
    identifiant = client.post("/api/conversations", json={}).json()["id"]

    reponse = client.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": identifiant,
              "messages": [{"role": "user", "content": "Salut"}], "stream": False},
    )
    assert reponse.status_code == 200
    assert reponse.json()["conversation_id"] == identifiant

    detail = client.get(f"/api/conversations/{identifiant}").json()
    assert detail["title"] == "Salut"          # titre déduit de la question
    assert detail["model"] == "ollama/mistral"
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "Bonjour"
    assert detail["messages"][1]["reasoning"] == "je réfléchis"
    assert detail["messages"][1]["duration_ms"] >= 0


def test_le_serveur_relit_l_historique(client):
    """Le client n'envoie que le message du tour : le serveur complète."""
    identifiant = client.post("/api/conversations", json={}).json()["id"]

    for texte in ["Premier", "Deuxième"]:
        client.post(
            "/api/chat",
            json={"model": "ollama/mistral", "conversation_id": identifiant,
                  "messages": [{"role": "user", "content": texte}], "stream": False},
        )

    messages = client.get(f"/api/conversations/{identifiant}").json()["messages"]
    assert [m["content"] for m in messages] == [
        "Premier", "Bonjour", "Deuxième", "Bonjour",
    ]


def test_streaming_enregistre_aussi(client):
    identifiant = client.post("/api/conversations", json={}).json()["id"]

    reponse = client.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": identifiant,
              "messages": [{"role": "user", "content": "Salut"}]},
    )
    evenements = parse_sse(reponse.text)
    assert any(nom == "done" for nom, _ in evenements)

    messages = client.get(f"/api/conversations/{identifiant}").json()["messages"]
    assert messages[1]["content"] == "Bonjour"
    assert messages[1]["reasoning"] == "je réfléchis"


def test_sans_conversation_id_rien_n_est_enregistre(client):
    client.post(
        "/api/chat",
        json={"model": "ollama/mistral",
              "messages": [{"role": "user", "content": "Salut"}], "stream": False},
    )
    assert client.get("/api/conversations").json() == []


def test_export_markdown(client):
    identifiant = client.post("/api/conversations", json={}).json()["id"]
    client.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": identifiant,
              "messages": [{"role": "user", "content": "Salut"}], "stream": False},
    )

    export = client.get(f"/api/conversations/{identifiant}/export")
    assert export.status_code == 200
    assert "markdown" in export.headers["content-type"]
    # Nom de fichier lisible dérivé du titre, pas un UUID nu.
    assert f'filename="salut-{identifiant[:8]}.md"' in export.headers["content-disposition"]
    texte = export.text
    assert "# Salut" in texte
    assert "## Assistant" in texte
    assert "Bonjour" in texte
    # Le raisonnement est conservé, mais replié.
    assert "<details><summary>Raisonnement</summary>" in texte


def test_nom_de_fichier_lisible():
    from app.routers.conversations import nom_de_fichier

    assert nom_de_fichier("Élève : où ?", "abcdef1234", "md") == "eleve-ou-abcdef12.md"
    assert nom_de_fichier("???", "abcdef1234", "md") == "conversation-abcdef12.md"


def test_export_json(client):
    identifiant = client.post("/api/conversations", json={}).json()["id"]
    client.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": identifiant,
              "messages": [{"role": "user", "content": "Salut"}], "stream": False},
    )
    export = client.get(f"/api/conversations/{identifiant}/export", params={"format": "json"})
    assert export.status_code == 200
    donnees = export.json()
    assert donnees["id"] == identifiant
    assert len(donnees["messages"]) == 2
