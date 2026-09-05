"""Tests de la couche fournisseurs (phase 2) — aucun appel réseau réel."""

from __future__ import annotations

import httpx
import pytest

from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    ProviderAuthError,
    ProviderNotConfigured,
    ProviderUnavailable,
    strip_provider_prefix,
)
from app.providers.ollama import OllamaProvider
from app.providers.openai_compat import MistralProvider, OpenAICompatibleProvider
from app.providers.registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest
from tests.conftest import ndjson, patch_provider, sse

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def request_for(model: str, system: str | None = None) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Bonjour")],
        system=system,
    )


async def collect(provider, request):
    """Consomme un flux et renvoie (texte complet, dernier fragment)."""
    text, last = "", None
    async for chunk in provider.stream_chat(request):
        text += chunk.delta
        last = chunk
    return text, last


# --------------------------------------------------------------------------- #
#  Préfixes
# --------------------------------------------------------------------------- #
def test_strip_prefix_ne_casse_pas_les_noms_avec_slash():
    assert strip_provider_prefix("ollama/mistral", "ollama") == "mistral"
    assert strip_provider_prefix("mistral:7b", "ollama") == "mistral:7b"
    # Un nom de modèle contenant lui-même une barre oblique doit survivre.
    assert (
        strip_provider_prefix("openai/mistralai/Mistral-7B", "openai")
        == "mistralai/Mistral-7B"
    )


# --------------------------------------------------------------------------- #
#  Ollama
# --------------------------------------------------------------------------- #
async def test_ollama_liste_les_modeles():
    provider = patch_provider(
        OllamaProvider("http://ollama.test:11434"),
        {
            "/api/tags": httpx.Response(
                200, json={"models": [{"name": "mistral:latest"}, {"name": "llama3"}]}
            )
        },
    )
    models = await provider.list_models()
    assert [m.id for m in models] == ["ollama/mistral:latest", "ollama/llama3"]
    assert models[0].provider == "ollama"
    await provider.aclose()


async def test_ollama_streaming_et_compteurs():
    provider = patch_provider(
        OllamaProvider("http://ollama.test:11434"),
        {
            "/api/chat": httpx.Response(
                200,
                content=ndjson(
                    {"message": {"content": "Bon"}, "done": False},
                    {"message": {"content": "jour"}, "done": False},
                    {"done": True, "prompt_eval_count": 7, "eval_count": 2},
                ),
            )
        },
    )
    text, last = await collect(provider, request_for("ollama/mistral"))
    assert text == "Bonjour"
    assert last.done is True
    assert last.usage == {"prompt_tokens": 7, "completion_tokens": 2}
    await provider.aclose()


async def test_ollama_injoignable_donne_une_erreur_explicite():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée", request=request)

    provider = OllamaProvider("http://ollama.test:11434")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))

    with pytest.raises(ProviderUnavailable) as exc:
        await provider.list_models()
    assert "ollama.test" in str(exc.value)
    assert exc.value.status_code == 503
    await provider.aclose()


# --------------------------------------------------------------------------- #
#  OpenAI et compatibles
# --------------------------------------------------------------------------- #
async def test_openai_streaming():
    provider = patch_provider(
        OpenAICompatibleProvider("https://api.openai.test/v1", api_key="k"),
        {
            "/v1/chat/completions": httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"content": "Bon"}}]},
                    {"choices": [{"delta": {"content": "jour"}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                    "[DONE]",
                ),
            )
        },
    )
    text, last = await collect(provider, request_for("openai/gpt-4o-mini"))
    assert text == "Bonjour"
    assert last.done is True
    assert last.finish_reason == "stop"
    assert last.usage["completion_tokens"] == 2
    await provider.aclose()


async def test_cle_invalide_donne_une_erreur_401():
    provider = patch_provider(
        OpenAICompatibleProvider("https://api.openai.test/v1", api_key="mauvaise"),
        {"/v1/models": httpx.Response(401, text='{"error":"clé invalide"}')},
    )
    with pytest.raises(ProviderAuthError) as exc:
        await provider.list_models()
    assert exc.value.status_code == 401
    await provider.aclose()


async def test_mistral_reutilise_le_contrat_openai():
    provider = patch_provider(
        MistralProvider("https://api.mistral.test/v1", api_key="k"),
        {"/v1/models": httpx.Response(200, json={"data": [{"id": "mistral-small"}]})},
    )
    models = await provider.list_models()
    assert models[0].id == "mistral/mistral-small"
    await provider.aclose()


# --------------------------------------------------------------------------- #
#  Anthropic
# --------------------------------------------------------------------------- #
async def test_anthropic_streaming_et_prompt_systeme_separe():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured.update(_json.loads(request.content))
        return httpx.Response(
            200,
            content=sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
                {"type": "content_block_delta",
                 "delta": {"type": "text_delta", "text": "Bon"}},
                {"type": "content_block_delta",
                 "delta": {"type": "text_delta", "text": "jour"}},
                {"type": "message_delta",
                 "delta": {"stop_reason": "end_turn"},
                 "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            ),
        )

    provider = AnthropicProvider("https://api.anthropic.test", api_key="k")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    text, last = await collect(
        provider, request_for("anthropic/claude-sonnet-4", system="Tu es concis.")
    )
    assert text == "Bonjour"
    assert last.usage == {"prompt_tokens": 9, "completion_tokens": 2}
    # Anthropic exige le prompt système dans un champ dédié, pas dans les messages.
    assert captured["system"] == "Tu es concis."
    assert all(m["role"] != "system" for m in captured["messages"])
    assert captured["max_tokens"] > 0
    await provider.aclose()


# --------------------------------------------------------------------------- #
#  Registre
# --------------------------------------------------------------------------- #
def test_registre_n_active_que_le_configure(settings_ollama_only):
    registry = ProviderRegistry(settings_ollama_only)
    assert registry.names == ["ollama"]
    with pytest.raises(ProviderNotConfigured):
        registry.get("openai")


def test_registre_resout_le_fournisseur_unique_sans_prefixe(settings_ollama_only):
    registry = ProviderRegistry(settings_ollama_only)
    assert registry.resolve("mistral:7b").name == "ollama"
    assert registry.resolve("ollama/mistral").name == "ollama"


def test_registre_refuse_un_modele_ambigu():
    from app.config import Settings

    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://o.test:11434", openai_api_key="k")
    )
    with pytest.raises(ProviderNotConfigured):
        registry.resolve("un-modele-sans-prefixe")


async def test_un_fournisseur_en_panne_ne_casse_pas_la_liste():
    from app.config import Settings

    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://o.test:11434", openai_api_key="k")
    )
    patch_provider(
        registry.get("ollama"),
        {"/api/tags": httpx.Response(200, json={"models": [{"name": "mistral"}]})},
    )
    patch_provider(
        registry.get("openai"),
        {"/v1/models": httpx.Response(500, text="panne")},
    )

    models = await registry.list_models()
    assert [m.id for m in models] == ["ollama/mistral"]

    statuses = {s.name: s for s in await registry.statuses()}
    assert statuses["ollama"].available is True
    assert statuses["openai"].available is False
    await registry.aclose()
