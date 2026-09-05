"""Interface commune à tous les fournisseurs de modèles.

Ajouter un fournisseur = créer une sous-classe de `ChatProvider` et l'enregistrer
dans `registry.py`. Rien d'autre dans l'application ne connaît les détails d'une API
particulière : tout passe par `list_models()` et `stream_chat()`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from app.schemas import ChatMessage, ChatRequest, ModelInfo

logger = logging.getLogger("larbinus.providers")


# --------------------------------------------------------------------------- #
#  Erreurs normalisées
# --------------------------------------------------------------------------- #
class ProviderError(Exception):
    """Erreur d'un fournisseur, traduite en réponse HTTP cohérente par l'API."""

    status_code: int = 502

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(f"[{provider}] {message}")

    def to_dict(self) -> dict:
        return {
            "error": {
                "provider": self.provider,
                "message": self.message,
                "type": type(self).__name__,
            }
        }


class ProviderUnavailable(ProviderError):
    """Le fournisseur est injoignable (réseau, DNS, timeout, service arrêté)."""

    status_code = 503


class ProviderAuthError(ProviderError):
    """Clé d'API absente, invalide ou sans droits suffisants."""

    status_code = 401


class ModelNotFound(ProviderError):
    """Le modèle demandé n'existe pas chez ce fournisseur."""

    status_code = 404


class ProviderNotConfigured(ProviderError):
    """Fournisseur inconnu ou désactivé faute de configuration."""

    status_code = 400


# --------------------------------------------------------------------------- #
#  Fragment de réponse
# --------------------------------------------------------------------------- #
@dataclass
class ChatChunk:
    """Un fragment de réponse en streaming.

    `delta` porte la réponse visible, `reasoning` le monologue interne des modèles
    de raisonnement (deepseek-r1, o-series, Claude en mode réflexion). Les deux sont
    séparés pour que l'interface puisse replier le second sans le confondre avec la
    réponse — les mélanger est irréversible côté client.

    Le dernier fragment porte `done=True` et, si le fournisseur les communique,
    les compteurs de jetons et la raison d'arrêt.
    """

    delta: str = ""
    reasoning: str = ""
    done: bool = False
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
#  Interface
# --------------------------------------------------------------------------- #
class ChatProvider(ABC):
    """Contrat que doit remplir tout fournisseur."""

    #: identifiant court, utilisé comme préfixe des modèles (`ollama/mistral`)
    name: str = "base"

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=30.0, pool=5.0),
            follow_redirects=True,
        )

    # -- à implémenter ------------------------------------------------------ #
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Modèles disponibles chez ce fournisseur."""

    @abstractmethod
    def _stream(self, request: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        """Streaming brut, `model` étant le nom natif (sans préfixe)."""

    # -- outillage commun --------------------------------------------------- #
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Point d'entrée public : retire le préfixe puis délègue à `_stream`."""
        model = strip_provider_prefix(request.model, self.name)
        async for chunk in self._stream(request, model):
            yield chunk

    @property
    def headers(self) -> dict[str, str]:
        return {}

    def split_system(self, request: ChatRequest) -> tuple[str | None, list[ChatMessage]]:
        """Sépare le prompt système du reste de la conversation.

        `request.system` l'emporte sur un message de rôle `system` présent dans
        l'historique — ainsi un persona ne peut pas être écrasé silencieusement.
        """
        history = [m for m in request.messages if m.role != "system"]
        if request.system:
            return request.system, history
        inline = next((m.content for m in request.messages if m.role == "system"), None)
        return inline, history

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- traduction des erreurs -------------------------------------------- #
    def _raise_http_error(self, status: int, body: str) -> None:
        detail = body.strip()[:400] or "réponse vide"
        if status in (401, 403):
            raise ProviderAuthError(self.name, f"authentification refusée : {detail}")
        if status == 404:
            raise ModelNotFound(self.name, f"ressource ou modèle introuvable : {detail}")
        if status == 429:
            raise ProviderError(self.name, f"quota ou débit dépassé : {detail}", status_code=429)
        raise ProviderError(self.name, f"erreur HTTP {status} : {detail}")

    def _wrap_transport_error(self, exc: Exception) -> ProviderUnavailable:
        return ProviderUnavailable(
            self.name,
            f"injoignable à l'adresse {self.base_url} ({type(exc).__name__}: {exc})",
        )


def strip_provider_prefix(model_id: str, provider_name: str) -> str:
    """`ollama/mistral` → `mistral` ; `mistral:7b` reste inchangé.

    Seul le préfixe correspondant au fournisseur est retiré, pour ne pas casser
    les noms de modèles qui contiennent eux-mêmes une barre oblique
    (par exemple `mistralai/Mistral-7B-Instruct` chez OpenRouter).
    """
    prefix = f"{provider_name}/"
    if model_id.startswith(prefix):
        return model_id[len(prefix) :]
    return model_id
