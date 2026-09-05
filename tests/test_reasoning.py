"""Modèles de raisonnement : le monologue interne ne doit jamais être confondu
avec la réponse, ni perdu en route."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.anthropic import AnthropicProvider
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest
from tests.conftest import ndjson, patch_provider, sse
from tests.test_chat import parse_sse

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


#: Flux Ollama tel que le renvoie deepseek-r1 : `thinking` à côté de `content`.
FLUX_DEEPSEEK = ndjson(
    {"message": {"role": "assistant", "thinking": "Voyons, "}, "done": False},
    {"message": {"role": "assistant", "thinking": "2+2 fait 4."}, "done": False},
    {"message": {"role": "assistant", "content": "Le résultat est 4."}, "done": False},
    {"done": True, "prompt_eval_count": 5, "eval_count": 12},
)


def registry_deepseek() -> ProviderRegistry:
    registry = ProviderRegistry(
        Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
    )
    patch_provider(
        registry.get("ollama"),
        {
            "/api/chat": httpx.Response(200, content=FLUX_DEEPSEEK),
            "/api/tags": httpx.Response(200, json={"models": [{"name": "deepseek-r1:8b"}]}),
        },
    )
    return registry


# --------------------------------------------------------------------------- #
#  Fournisseurs
# --------------------------------------------------------------------------- #
async def test_ollama_separe_thinking_et_content():
    from app.providers.ollama import OllamaProvider

    provider = patch_provider(
        OllamaProvider("http://ollama.test:11434"),
        {"/api/chat": httpx.Response(200, content=FLUX_DEEPSEEK)},
    )
    reponse, raisonnement = "", ""
    async for chunk in provider.stream_chat(
        ChatRequest(model="ollama/deepseek-r1:8b",
                    messages=[ChatMessage(role="user", content="2+2 ?")])
    ):
        reponse += chunk.delta
        raisonnement += chunk.reasoning

    assert reponse == "Le résultat est 4."
    assert raisonnement == "Voyons, 2+2 fait 4."
    await provider.aclose()


async def test_openai_compat_accepte_reasoning_content_et_reasoning():
    provider = patch_provider(
        OpenAICompatibleProvider("https://api.test/v1", api_key="k"),
        {
            "/v1/chat/completions": httpx.Response(
                200,
                content=sse(
                    {"choices": [{"delta": {"reasoning_content": "hmm "}}]},   # DeepSeek
                    {"choices": [{"delta": {"reasoning": "voyons"}}]},          # OpenRouter
                    {"choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}]},
                    "[DONE]",
                ),
            )
        },
    )
    reponse, raisonnement = "", ""
    async for chunk in provider.stream_chat(
        ChatRequest(model="openai/r1", messages=[ChatMessage(role="user", content="2+2 ?")])
    ):
        reponse += chunk.delta
        raisonnement += chunk.reasoning

    assert reponse == "4"
    assert raisonnement == "hmm voyons"
    await provider.aclose()


async def test_anthropic_thinking_delta():
    provider = AnthropicProvider("https://api.anthropic.test", api_key="k")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=sse(
                    {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                    {"type": "content_block_delta",
                     "delta": {"type": "thinking_delta", "thinking": "je réfléchis"}},
                    {"type": "content_block_delta",
                     "delta": {"type": "text_delta", "text": "4"}},
                    {"type": "message_stop"},
                ),
            )
        )
    )
    reponse, raisonnement = "", ""
    async for chunk in provider.stream_chat(
        ChatRequest(model="anthropic/claude", messages=[ChatMessage(role="user", content="2+2 ?")])
    ):
        reponse += chunk.delta
        raisonnement += chunk.reasoning

    assert reponse == "4"
    assert raisonnement == "je réfléchis"
    await provider.aclose()


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #
def test_api_chat_emet_un_evenement_reasoning_distinct():
    with TestClient(app) as client:
        app.state.registry = registry_deepseek()
        response = client.post(
            "/api/chat",
            json={"model": "ollama/deepseek-r1:8b",
                  "messages": [{"role": "user", "content": "2+2 ?"}]},
        )

    events = parse_sse(response.text)
    raisonnement = "".join(
        json.loads(d)["reasoning"] for name, d in events if name == "reasoning"
    )
    reponse = "".join(json.loads(d)["delta"] for name, d in events if name == "delta")

    assert raisonnement == "Voyons, 2+2 fait 4."
    assert reponse == "Le résultat est 4."


def test_api_chat_non_streaming_separe_les_deux():
    with TestClient(app) as client:
        app.state.registry = registry_deepseek()
        response = client.post(
            "/api/chat",
            json={"model": "ollama/deepseek-r1:8b",
                  "messages": [{"role": "user", "content": "2+2 ?"}],
                  "stream": False},
        )
    body = response.json()
    assert body["content"] == "Le résultat est 4."
    assert body["reasoning"] == "Voyons, 2+2 fait 4."


def test_pas_de_champ_reasoning_pour_un_modele_classique():
    """Absence du champ, pas une chaîne vide : le client doit pouvoir distinguer."""
    from tests.test_chat import registry_ollama

    with TestClient(app) as client:
        app.state.registry = registry_ollama()
        response = client.post(
            "/api/chat",
            json={"model": "ollama/mistral",
                  "messages": [{"role": "user", "content": "Salut"}],
                  "stream": False},
        )
    assert "reasoning" not in response.json()


def test_openai_expose_reasoning_content():
    with TestClient(app) as client:
        app.state.registry = registry_deepseek()
        response = client.post(
            "/v1/chat/completions",
            json={"model": "ollama/deepseek-r1:8b",
                  "messages": [{"role": "user", "content": "2+2 ?"}]},
        )
    message = response.json()["choices"][0]["message"]
    assert message["content"] == "Le résultat est 4."
    assert message["reasoning_content"] == "Voyons, 2+2 fait 4."


def test_openai_streaming_reasoning_hors_du_content():
    """Un client OpenAI strict ignore `reasoning_content` : le contenu doit
    rester propre, sans monologue interne mélangé dedans."""
    with TestClient(app) as client:
        app.state.registry = registry_deepseek()
        response = client.post(
            "/v1/chat/completions",
            json={"model": "ollama/deepseek-r1:8b",
                  "messages": [{"role": "user", "content": "2+2 ?"}],
                  "stream": True},
        )
    chunks = [json.loads(d) for _, d in parse_sse(response.text) if d != "[DONE]"]
    contenu = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    raisonnement = "".join(
        c["choices"][0]["delta"].get("reasoning_content", "") for c in chunks
    )
    assert contenu == "Le résultat est 4."
    assert raisonnement == "Voyons, 2+2 fait 4."
