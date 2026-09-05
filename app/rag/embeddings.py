"""Calcul des embeddings.

Deux sources possibles : une instance Ollama (par défaut, `nomic-embed-text`)
ou une API compatible OpenAI. Le choix est indépendant du modèle de
conversation : on peut très bien discuter avec Claude tout en vectorisant en
local, ce qui évite d'envoyer chez un tiers l'intégralité des documents
indexés.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("larbinus.rag.embeddings")


class EmbeddingIndisponible(Exception):
    """Le service d'embeddings est injoignable ou refuse la demande."""


class ClientEmbeddings:
    """Interface commune aux deux fournisseurs d'embeddings."""

    def __init__(self, base_url: str, modele: str, api_key: str | None = None,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.modele = modele
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=30.0, pool=5.0)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def vectoriser(self, textes: list[str]) -> list[list[float]]:
        raise NotImplementedError


class EmbeddingsOllama(ClientEmbeddings):
    async def vectoriser(self, textes: list[str]) -> list[list[float]]:
        if not textes:
            return []
        try:
            reponse = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.modele, "input": textes},
            )
            if reponse.status_code == 404:
                # Les versions antérieures d'Ollama n'exposent que
                # /api/embeddings, qui ne traite qu'un texte à la fois.
                return await self._vectoriser_ancienne_api(textes)
            if reponse.status_code != 200:
                raise EmbeddingIndisponible(
                    f"Ollama a répondu {reponse.status_code} : {reponse.text[:200]}"
                )
            vecteurs = reponse.json().get("embeddings")
            if not vecteurs:
                raise EmbeddingIndisponible("Réponse d'Ollama sans embeddings.")
            return vecteurs
        except httpx.HTTPError as exc:
            raise EmbeddingIndisponible(
                f"Ollama injoignable à {self.base_url} ({exc})"
            ) from exc

    async def _vectoriser_ancienne_api(self, textes: list[str]) -> list[list[float]]:
        vecteurs = []
        for texte in textes:
            reponse = await self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.modele, "prompt": texte},
            )
            if reponse.status_code != 200:
                raise EmbeddingIndisponible(
                    f"Ollama a répondu {reponse.status_code} : {reponse.text[:200]}"
                )
            vecteurs.append(reponse.json()["embedding"])
        return vecteurs


class EmbeddingsOpenAI(ClientEmbeddings):
    async def vectoriser(self, textes: list[str]) -> list[list[float]]:
        if not textes:
            return []
        try:
            reponse = await self._client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.modele, "input": textes},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingIndisponible(
                f"Service d'embeddings injoignable à {self.base_url} ({exc})"
            ) from exc
        if reponse.status_code != 200:
            raise EmbeddingIndisponible(
                f"Le service a répondu {reponse.status_code} : {reponse.text[:200]}"
            )
        donnees = sorted(reponse.json()["data"], key=lambda e: e["index"])
        return [entree["embedding"] for entree in donnees]


def construire_client(settings) -> ClientEmbeddings | None:
    """Construit le client d'embeddings, ou `None` si rien n'est configuré."""
    fournisseur = (settings.embedding_provider or "").lower()

    if fournisseur == "ollama":
        if not settings.ollama_base_url:
            logger.warning(
                "EMBEDDING_PROVIDER=ollama mais OLLAMA_BASE_URL est vide : "
                "l'indexation restera indisponible."
            )
            return None
        return EmbeddingsOllama(
            base_url=settings.ollama_base_url,
            modele=settings.embedding_model,
            timeout=settings.request_timeout,
        )

    if fournisseur == "openai":
        if not settings.openai_api_key:
            logger.warning(
                "EMBEDDING_PROVIDER=openai mais OPENAI_API_KEY est vide : "
                "l'indexation restera indisponible."
            )
            return None
        return EmbeddingsOpenAI(
            base_url=settings.openai_base_url,
            modele=settings.embedding_model,
            api_key=settings.openai_api_key,
            timeout=settings.request_timeout,
        )

    if fournisseur:
        logger.warning("EMBEDDING_PROVIDER inconnu : « %s »", fournisseur)
    return None
