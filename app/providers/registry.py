"""Registre des fournisseurs actifs.

Un fournisseur n'est instancié que si sa configuration est présente : pas de clé
(ou pas d'URL) signifie pas de fournisseur. L'application n'a donc jamais à tester
« est-ce que telle API est configurée ? » ailleurs qu'ici.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    ChatProvider,
    ProviderError,
    ProviderNotConfigured,
)
from app.providers.ollama import OllamaProvider
from app.providers.openai_compat import MistralProvider, OpenAICompatibleProvider
from app.schemas import ModelInfo, ProviderStatus

logger = logging.getLogger("larbinus.registry")


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._providers: dict[str, ChatProvider] = {}

        timeout = settings.request_timeout

        if settings.ollama_base_url:
            self._providers["ollama"] = OllamaProvider(
                base_url=settings.ollama_base_url, timeout=timeout
            )
        if settings.openai_api_key:
            self._providers["openai"] = OpenAICompatibleProvider(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=timeout,
            )
        if settings.anthropic_api_key:
            self._providers["anthropic"] = AnthropicProvider(
                base_url=settings.anthropic_base_url,
                api_key=settings.anthropic_api_key,
                timeout=timeout,
            )
        if settings.mistral_api_key:
            self._providers["mistral"] = MistralProvider(
                base_url=settings.mistral_base_url,
                api_key=settings.mistral_api_key,
                timeout=timeout,
            )

    # -- accès -------------------------------------------------------------- #
    @property
    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> ChatProvider:
        provider = self._providers.get(name)
        if provider is None:
            configured = ", ".join(self.names) or "aucun"
            raise ProviderNotConfigured(
                name,
                f"fournisseur inconnu ou non configuré (actifs : {configured})",
            )
        return provider

    def resolve(self, model_id: str) -> ChatProvider:
        """Détermine le fournisseur à partir d'un identifiant `fournisseur/modèle`.

        Sans préfixe reconnu, on retombe sur `DEFAULT_PROVIDER`, puis — s'il n'y en a
        qu'un seul de configuré — sur celui-là. Cela permet d'appeler simplement
        `mistral:7b` quand une seule instance Ollama est branchée.
        """
        prefix = model_id.split("/", 1)[0] if "/" in model_id else None
        if prefix and prefix in self._providers:
            return self._providers[prefix]

        default = self._settings.default_provider
        if default and default in self._providers:
            return self._providers[default]

        if len(self._providers) == 1:
            return next(iter(self._providers.values()))

        raise ProviderNotConfigured(
            prefix or "?",
            f"impossible de déterminer le fournisseur pour « {model_id} » ; "
            "préfixez le modèle (ex. `ollama/mistral`) ou renseignez DEFAULT_PROVIDER",
        )

    # -- agrégation --------------------------------------------------------- #
    async def list_models(self) -> list[ModelInfo]:
        """Modèles de tous les fournisseurs actifs.

        Un fournisseur en échec ne fait pas échouer l'ensemble : il est simplement
        absent du résultat, et la raison part dans les logs.
        """
        results = await asyncio.gather(
            *(p.list_models() for p in self._providers.values()),
            return_exceptions=True,
        )
        models: list[ModelInfo] = []
        for name, result in zip(self._providers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("Fournisseur « %s » indisponible : %s", name, result)
                continue
            models.extend(result)
        return sorted(models, key=lambda m: m.id)

    async def statuses(self) -> list[ProviderStatus]:
        """État détaillé de chaque fournisseur, pour le diagnostic."""
        results = await asyncio.gather(
            *(p.list_models() for p in self._providers.values()),
            return_exceptions=True,
        )
        statuses: list[ProviderStatus] = []
        for name, result in zip(self._providers, results, strict=True):
            if isinstance(result, ProviderError):
                statuses.append(ProviderStatus(name=name, available=False, detail=result.message))
            elif isinstance(result, BaseException):
                statuses.append(ProviderStatus(name=name, available=False, detail=str(result)))
            else:
                statuses.append(
                    ProviderStatus(name=name, available=True, model_count=len(result))
                )
        return statuses

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
