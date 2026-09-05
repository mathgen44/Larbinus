"""Personas et migration du schéma (phase 6)."""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.registry import ProviderRegistry
from app.storage.db import Database
from tests.conftest import ndjson, patch_provider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client(tmp_path):
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
                        {"message": {"content": "Bonjour"}, "done": False},
                        {"done": True, "prompt_eval_count": 3, "eval_count": 1},
                    ),
                ),
                "/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]}),
            },
        )
        app.state.registry = registry
        yield testeur


# --------------------------------------------------------------------------- #
#  Migration
# --------------------------------------------------------------------------- #
async def test_migration_depuis_une_base_v1_sans_perte(tmp_path):
    """Une base déjà déployée en v1 doit se mettre à jour en gardant ses données."""
    chemin = tmp_path / "ancienne.db"

    # Reconstitution d'une base v1 : schéma d'origine, une conversation, un message.
    ancienne = sqlite3.connect(chemin)
    ancienne.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT, system TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL, content TEXT NOT NULL, reasoning TEXT, model TEXT,
            provider TEXT, usage_json TEXT, duration_ms INTEGER, created_at TEXT NOT NULL);
        PRAGMA user_version=1;
        """
    )
    ancienne.execute(
        "INSERT INTO conversations VALUES ('c1','Ancienne','ollama/mistral',NULL,'t','t')"
    )
    ancienne.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at)"
        " VALUES ('c1','user','Question d''avant',​'t')".replace("​", "")
    )
    ancienne.commit()
    ancienne.close()

    db = Database(chemin)
    await db.connect()

    assert (await db.conversation("c1"))["title"] == "Ancienne"
    assert len(await db.messages("c1")) == 1
    # Les nouvelles colonnes existent, les personas d'exemple sont là.
    assert (await db.conversation("c1"))["persona_id"] is None
    assert len(await db.liste_personas()) == 4
    # La migration v3 (RAG) doit s'appliquer dans la foulée, sans rupture.
    assert await db._lire("SELECT COUNT(*) AS n FROM documents")
    assert (await db.conversation("c1"))["rag"] == 0
    await db.close()

    # Réouverture : la migration ne doit pas rejouer ni dupliquer les exemples.
    db2 = Database(chemin)
    await db2.connect()
    assert len(await db2.liste_personas()) == 4
    await db2.close()


async def test_personas_supprimes_ne_reviennent_pas(tmp_path):
    db = Database(tmp_path / "x.db")
    await db.connect()
    for persona in await db.liste_personas():
        await db.supprimer_persona(persona["id"])
    await db.close()

    db2 = Database(tmp_path / "x.db")
    await db2.connect()
    assert await db2.liste_personas() == []
    await db2.close()


# --------------------------------------------------------------------------- #
#  CRUD
# --------------------------------------------------------------------------- #
def test_personas_exemple_presents(client):
    personas = client.get("/api/personas").json()
    noms = {p["name"] for p in personas}
    assert {"Assistant", "Développeur", "Homelab", "Traducteur"} <= noms
    assert all(p["system"] for p in personas)


def test_crud_persona(client):
    creation = client.post(
        "/api/personas",
        json={"name": "Poète", "system": "Tu écris en alexandrins.",
              "temperature": 1.2, "icon": "🪶"},
    )
    assert creation.status_code == 201
    identifiant = creation.json()["id"]

    modifie = client.patch(f"/api/personas/{identifiant}", json={"temperature": 0.9})
    assert modifie.json()["temperature"] == 0.9
    assert modifie.json()["name"] == "Poète"      # champ non fourni : inchangé

    assert client.delete(f"/api/personas/{identifiant}").status_code == 204
    assert client.get(f"/api/personas/{identifiant}").status_code == 404


def test_temperature_hors_bornes_refusee(client):
    reponse = client.post("/api/personas", json={"name": "X", "temperature": 5})
    assert reponse.status_code == 422


# --------------------------------------------------------------------------- #
#  Application à une conversation
# --------------------------------------------------------------------------- #
def test_conversation_herite_du_persona(client):
    persona = next(p for p in client.get("/api/personas").json() if p["name"] == "Homelab")

    conversation = client.post(
        "/api/conversations", json={"persona_id": persona["id"], "model": "ollama/mistral"}
    ).json()

    assert conversation["persona_id"] == persona["id"]
    assert conversation["system"] == persona["system"]
    assert conversation["temperature"] == persona["temperature"]
    # Le titre reste celui par défaut : la première question le remplacera.
    assert conversation["title"] == "Nouvelle conversation"


def test_reglages_explicites_priment_sur_le_persona(client):
    persona = next(p for p in client.get("/api/personas").json() if p["name"] == "Homelab")
    conversation = client.post(
        "/api/conversations",
        json={"persona_id": persona["id"], "system": "Consigne du jour", "temperature": 0.1},
    ).json()
    assert conversation["system"] == "Consigne du jour"
    assert conversation["temperature"] == 0.1


def test_persona_inconnu_donne_404(client):
    reponse = client.post("/api/conversations", json={"persona_id": "inexistant"})
    assert reponse.status_code == 404


def test_le_prompt_du_persona_est_envoye_au_modele(client):
    """Le client n'a pas à renvoyer la consigne : le serveur la relit."""
    persona = client.post(
        "/api/personas", json={"name": "Bref", "system": "Réponds en trois mots."}
    ).json()
    conversation = client.post(
        "/api/conversations", json={"persona_id": persona["id"]}
    ).json()

    envoye = {}
    provider = app.state.registry.get("ollama")
    original = provider._client

    def intercepte(request: httpx.Request) -> httpx.Response:
        import json as _json

        envoye.update(_json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Trois mots exactement"}, "done": False},
                {"done": True},
            ),
        )

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(intercepte))
    try:
        client.post(
            "/api/chat",
            json={"model": "ollama/mistral", "conversation_id": conversation["id"],
                  "messages": [{"role": "user", "content": "Salut"}], "stream": False},
        )
    finally:
        provider._client = original

    assert envoye["messages"][0] == {"role": "system", "content": "Réponds en trois mots."}


def test_supprimer_un_persona_ne_touche_pas_aux_conversations(client):
    """La conversation garde sa copie du prompt : seul le lien est coupé."""
    persona = client.post(
        "/api/personas", json={"name": "Éphémère", "system": "Consigne conservée"}
    ).json()
    conversation = client.post(
        "/api/conversations", json={"persona_id": persona["id"]}
    ).json()

    client.delete(f"/api/personas/{persona['id']}")

    rechargee = client.get(f"/api/conversations/{conversation['id']}").json()
    assert rechargee["persona_id"] is None
    assert rechargee["system"] == "Consigne conservée"
