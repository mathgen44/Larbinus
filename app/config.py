"""Configuration de Larbinus.

Toute la configuration passe par les variables d'environnement (fichier `.env`).
Aucun secret n'est écrit en dur : un fournisseur dont la clé (ou l'URL) est absente
est simplement désactivé au démarrage.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "Larbinus"
    version: str = "0.1.0"
    log_level: str = "INFO"

    # Port d'écoute *à l'intérieur* du conteneur.
    # Le port publié sur le LAN est piloté par LARBINUS_PORT dans docker-compose.yml.
    internal_port: int = 8080

    # Si renseignée, toutes les routes /api et /v1 exigent cette clé
    # (en-tête `X-API-Key` ou `Authorization: Bearer <clé>`).
    larbinus_api_key: str | None = None

    # Origines autorisées pour le navigateur ; "*" en développement.
    cors_origins: str = "*"

    # Répertoire persistant (SQLite, index RAG, documents).
    data_dir: str = "/data"

    # --- Fournisseurs ---
    ollama_base_url: str | None = None          # ex. http://192.168.0.50:11434

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"

    mistral_api_key: str | None = None
    mistral_base_url: str = "https://api.mistral.ai/v1"

    # --- Valeurs par défaut de l'interface ---
    default_provider: str | None = None
    default_model: str | None = None

    # --- Réseau ---
    request_timeout: float = 120.0

    @property
    def enabled_providers(self) -> list[str]:
        """Fournisseurs utilisables compte tenu de la configuration présente."""
        enabled: list[str] = []
        if self.ollama_base_url:
            enabled.append("ollama")
        if self.openai_api_key:
            enabled.append("openai")
        if self.anthropic_api_key:
            enabled.append("anthropic")
        if self.mistral_api_key:
            enabled.append("mistral")
        return enabled

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
