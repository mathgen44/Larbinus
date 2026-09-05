"""Modèles de données partagés entre l'API et la couche fournisseurs."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    """Requête de conversation, indépendante du fournisseur."""

    model: str = Field(
        ...,
        description="Identifiant complet « fournisseur/modèle », ex. `ollama/mistral`. "
        "Un identifiant sans préfixe utilise le fournisseur par défaut.",
        examples=["ollama/mistral"],
    )
    messages: list[ChatMessage] = Field(..., min_length=1)
    system: str | None = Field(
        default=None,
        description="Prompt système. Prioritaire sur un éventuel message de rôle `system`.",
    )
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = True
    conversation_id: str | None = Field(
        default=None,
        description="Si fourni, la base devient la source de vérité : le serveur "
        "relit l'historique enregistré, y ajoute `messages`, puis enregistre la "
        "question et la réponse. Le client n'envoie alors que le message du tour.",
    )


class ModelInfo(BaseModel):
    """Un modèle exposé par un fournisseur."""

    id: str = Field(..., description="Identifiant complet, ex. `ollama/mistral`")
    name: str = Field(..., description="Nom natif chez le fournisseur")
    provider: str
    context_length: int | None = None


class ProviderStatus(BaseModel):
    name: str
    available: bool
    detail: str | None = None
    model_count: int | None = None
