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

    # Si renseignée, les routes /v1 (clients OpenAI, n8n, scripts) exigent
    # cette clé, via `X-API-Key` ou `Authorization: Bearer <clé>`.
    larbinus_api_key: str | None = None

    # Étend l'exigence de clé aux routes /api, donc à l'interface web
    # elle-même. Laissé à faux, l'interface reste libre sur le LAN — et avec
    # elle les documents indexés. À passer à vrai dès que Larbinus est exposé
    # au-delà d'un réseau de confiance.
    larbinus_protect_ui: bool = False

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

    # --- Documents et RAG ---
    #: Fournisseur d'embeddings : « ollama », « openai », ou vide pour désactiver
    #: l'indexation. Indépendant du modèle de conversation : on peut discuter
    #: avec une API en ligne tout en vectorisant les documents en local.
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"

    #: Dossier surveillé, monté dans le conteneur.
    documents_dir: str = "/documents"

    rag_top_k: int = 5
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150
    max_document_bytes: int = 25 * 1024 * 1024

    # --- Outils ---
    #: Inventaire des machines joignables en SSH, au format
    #: `nom=utilisateur@hote[:port]`, séparées par des virgules.
    #: Vide : l'outil ssh reste inactif.
    ssh_hosts: str = ""
    ssh_key_path: str | None = None
    ssh_known_hosts: str | None = None
    ssh_timeout: float = 30.0
    ssh_connect_timeout: float = 5.0

    #: Longueur maximale d'une sortie d'outil renvoyée au modèle. Au-delà, le
    #: début et la fin sont conservés : sur un journal, le milieu n'apprend rien.
    tool_output_limit: int = 6000

    #: Nombre d'allers-retours automatiques avec le modèle après exécution d'un
    #: outil. Un plafond bas évite qu'une boucle mal engagée ne parte seule.
    tool_max_iterations: int = 3

    # --- Journalisation ---
    #: « texte » pour un journal lisible à l'œil, « json » pour un journal
    #: exploitable par un collecteur.
    log_format: str = "texte"

    # --- Limitation de débit ---
    #: Requêtes autorisées par adresse et par fenêtre. 0 désactive.
    rate_limit_requests: int = 120
    rate_limit_window: int = 60

    #: Adresses des reverse proxys autorisés à annoncer l'IP réelle du client
    #: via `X-Forwarded-For`. Sans cette liste, l'en-tête est ignoré.
    trusted_proxies: str = ""

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

    @property
    def trusted_proxy_set(self) -> set[str]:
        return {p.strip() for p in self.trusted_proxies.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
