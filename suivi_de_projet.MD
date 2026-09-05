# Larbinus — Suivi de projet

> **Objectif** : un petit conteneur Docker, déployable via `docker compose`, capable
> d'interfacer des modèles d'IA en ligne et/ou une instance locale (Ollama), avec une
> interface de chat web et une API réutilisable dans le homelab.

| | |
|---|---|
| **Dépôt** | https://github.com/mathgen44/Larbinus |
| **Dossier de travail** | `D:\GIT\Larbinus` |
| **Machine de déploiement** | `192.168.0.40` — accès prévu sur `http://192.168.0.40:8474` |
| **Phase en cours** | Phase 2 — Couche fournisseurs (terminée, à valider en réel) → Phase 3 |
| **Dernière mise à jour** | 2026-09-05 |

**Règle de tenue du fichier** : à chaque étape, cocher les tâches terminées, mettre à jour
la « Phase en cours » et la date, et consigner tout bug rencontré dans le § 7 avec sa
correction. Toute décision d'architecture qui change le cap va dans le § 8.

---

## 1. Décisions validées

| Sujet | Décision | Date |
|---|---|---|
| Interface | Web UI de chat **+** API REST/SSE | 2026-09-05 |
| Fournisseurs v1 | Ollama (local), OpenAI & compatibles, Anthropic, Mistral AI | 2026-09-05 |
| Stack | Python 3.12 + FastAPI | 2026-09-05 |
| Fonctions v1+ | Historique persisté, personas/prompts système, RAG documents locaux | 2026-09-05 |
| Déploiement | Image unique, `docker compose up -d`, config par `.env` | 2026-09-05 |
| API compatible OpenAI | **Oui** — `/v1/chat/completions` et `/v1/models` en plus de l'API native | 2026-09-05 |
| Licence | MIT | 2026-09-05 |
| Port publié | `8474` par défaut (8080 occupé par SearXNG sur la machine cible) | 2026-09-05 |

**Pourquoi Python/FastAPI** : écosystème IA le plus riche (SDK officiels des quatre
fournisseurs, briques RAG matures), streaming SSE natif et simple, image finale légère
(`python:3.12-slim`), et cohérent avec le reste du homelab.

---

## 2. Architecture cible

```
                    ┌─────────────────────────────┐
   Navigateur ──────▶  Larbinus (conteneur)       │
   / n8n / curl     │                             │
                    │  FastAPI                    │
                    │   ├── /api/chat  (SSE)      │
                    │   ├── /v1/chat/completions  │  ◀── compatible OpenAI
                    │   ├── /v1/models            │
                    │   ├── /api/models           │
                    │   ├── /api/conversations    │
                    │   ├── /api/personas         │
                    │   ├── /api/documents (RAG)  │
                    │   └── /  (UI statique)      │
                    │                             │
                    │  Couche « providers »       │
                    │   ├── ollama                │──▶ http://192.168.0.139:11434 (configurable)
                    │   ├── openai-compatible     │──▶ api.openai.com / OpenRouter / vLLM
                    │   ├── anthropic             │──▶ api.anthropic.com
                    │   └── mistral               │──▶ api.mistral.ai
                    │                             │
                    │  SQLite (volume persistant) │
                    └─────────────────────────────┘
```

**Principe directeur** : une seule interface interne `ChatProvider` (méthodes
`list_models()` et `stream_chat()`), chaque fournisseur en étant une implémentation.
Ajouter un fournisseur = ajouter un fichier, sans toucher au reste.

### Arborescence prévue

```
Larbinus/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── README.md
├── suivi_de_projet.MD
├── requirements.txt
├── app/
│   ├── main.py              # points d'entrée FastAPI
│   ├── config.py            # chargement .env (pydantic-settings)
│   ├── schemas.py           # modèles Pydantic (requêtes/réponses)
│   ├── providers/
│   │   ├── base.py          # interface ChatProvider
│   │   ├── registry.py      # découverte + activation selon .env
│   │   ├── ollama.py
│   │   ├── openai_compat.py
│   │   ├── anthropic.py
│   │   └── mistral.py
│   ├── storage/
│   │   ├── db.py            # SQLite + migrations légères
│   │   └── models.py        # conversations, messages, personas
│   ├── rag/                 # phase 7
│   └── static/              # UI de chat (HTML/CSS/JS sans build)
└── tests/
```

---

## 3. Feuille de route

### Phase 0 — Cadrage & initialisation ✅ *terminée*

- [x] Définir interface, fournisseurs, stack et périmètre fonctionnel
- [x] Rédiger ce fichier de suivi
- [x] Initialiser le dépôt local (`git init` + remote `origin`) — le dépôt distant est
      volontairement vide, les fichiers seront poussés une fois prêts
- [x] Ajouter `.gitignore` (`.env`, `__pycache__/`, `data/`, `*.db`)
- [x] Rédiger le `README.md` (démarrage rapide, configuration, développement local)
- [x] Choisir la licence : **MIT**, fichier `LICENSE` ajouté

### Phase 1 — Socle conteneur ✅ *terminée*

- [x] `requirements.txt` (fastapi, uvicorn, httpx, pydantic-settings) + `requirements-dev.txt`
- [x] `app/config.py` : lecture des variables d'environnement, aucun secret en dur ;
      propriété `enabled_providers` qui désactive tout fournisseur non configuré
- [x] `app/main.py` : `GET /health` renvoyant statut, version et fournisseurs actifs ;
      `GET /` de courtoisie ; CORS configurable ; création du `DATA_DIR` au démarrage
- [x] `Dockerfile` multi-stage (venv), utilisateur non-root `larbinus` (uid 1000), `HEALTHCHECK`
- [x] `docker-compose.yml` : port publié configurable, volume `./data`,
      `restart: unless-stopped`, alias `host.docker.internal` pour un Ollama sur l'hôte
- [x] `.env.example` documenté
- [x] `tests/test_health.py` — 3 tests de fumée, tous au vert
- [ ] **Vérification** : `docker compose up -d --build` puis `/health` répond depuis le LAN

### Phase 2 — Couche fournisseurs ✅ *terminée (validée par tests, reste à confirmer en réel)*

- [x] `app/schemas.py` : `ChatMessage`, `ChatRequest`, `ModelInfo`, `ProviderStatus`
- [x] `providers/base.py` : interface `ChatProvider` + erreurs normalisées
      (`ProviderUnavailable` 503, `ProviderAuthError` 401, `ModelNotFound` 404,
      `ProviderNotConfigured` 400) + `ChatChunk`
- [x] `providers/registry.py` : n'instancie un fournisseur que si sa clé/URL est renseignée ;
      résolution `fournisseur/modèle` avec repli sur `DEFAULT_PROVIDER` puis sur le
      fournisseur unique ; agrégation tolérante aux pannes
- [x] Implémenter `ollama` (streaming NDJSON, modèles via `/api/tags`)
- [x] Implémenter `openai_compat` (base_url configurable → OpenAI, OpenRouter, vLLM, LM Studio)
- [x] Implémenter `anthropic` (`/v1/messages`, prompt système séparé, SSE typé)
- [x] Implémenter `mistral` (sous-classe d'`openai_compat`)
- [x] Timeouts distincts connexion/lecture ; un fournisseur injoignable est ignoré
      dans `/api/models` au lieu de faire échouer l'appel
- [x] `GET /api/models` (agrégation) et `GET /api/providers` (diagnostic par fournisseur)
- [x] 17 tests au vert, transport HTTP simulé, aucun appel réseau
- [ ] **Vérification en réel** : `GET /api/models` renvoie bien les modèles de
      l'instance Ollama une fois le conteneur déployé

### Phase 3 — API de chat

- [ ] `POST /api/chat` : `{provider, model, messages, system, temperature, stream}`
- [ ] Streaming SSE token par token, avec annulation propre côté client
- [ ] Comptage des tokens et durée de réponse dans l'événement final
- [ ] Erreurs normalisées (fournisseur injoignable, clé invalide, modèle inconnu)
- [ ] **Couche compatible OpenAI** : `POST /v1/chat/completions` (streaming `data:` +
      `[DONE]`) et `GET /v1/models`, modèles exposés sous la forme `fournisseur/modèle`
- [ ] Accepter `Authorization: Bearer` en plus de `X-API-Key`
- [ ] **Vérification** : `curl -N` sur les 4 fournisseurs, réponse en flux
- [ ] **Vérification** : un nœud n8n « OpenAI » pointé sur Larbinus fonctionne sans adaptateur

### Phase 4 — Interface web

- [ ] Page de chat statique, sans étape de build (HTML + JS vanilla)
- [ ] Sélecteur fournisseur/modèle alimenté par `/api/models`
- [ ] Rendu Markdown + blocs de code avec bouton copier
- [ ] Thème clair/sombre, mise en page responsive (utilisable au téléphone)
- [ ] Indicateur de streaming et bouton « stop »
- [ ] **Vérification** : conversation complète de bout en bout dans le navigateur

### Phase 5 — Persistance

- [ ] Schéma SQLite : `conversations`, `messages`, `settings`
- [ ] `GET/POST/DELETE /api/conversations`, reprise d'un fil existant
- [ ] Liste latérale des conversations dans l'UI, renommage, suppression
- [ ] Export d'une conversation en Markdown / JSON
- [ ] **Vérification** : redémarrage du conteneur, l'historique survit

### Phase 6 — Personas (« larbins »)

- [ ] Table `personas` : nom, prompt système, fournisseur/modèle par défaut, paramètres
- [ ] CRUD API + écran de gestion dans l'UI
- [ ] Personas préchargés à titre d'exemple (rédaction, code, traduction, homelab)
- [ ] **Vérification** : ouvrir une conversation depuis un persona applique bien ses réglages

### Phase 7 — RAG documents locaux

- [ ] Choix du stockage vectoriel (SQLite + `sqlite-vec`, ou Chroma en volume)
- [ ] Ingestion : PDF, DOCX, MD, TXT — découpage en fragments + métadonnées
- [ ] Embeddings via Ollama (`nomic-embed-text`) avec repli sur API en ligne
- [ ] Recherche sémantique et injection du contexte dans le prompt
- [ ] Réponses sourcées (extraits + nom de fichier affichés dans l'UI)
- [ ] Écran d'upload et de gestion des collections
- [ ] **Vérification** : question dont la réponse n'existe que dans un document ingéré

### Phase 8 — Sécurité & mise en production homelab

- [ ] Authentification simple de l'API (jeton `X-API-Key`) activable par `.env`
- [ ] CORS restreint, limitation de débit basique
- [ ] Logs structurés, niveau réglable
- [ ] Reverse proxy (Traefik/NPM) + HTTPS, documenté dans le README
- [ ] Publication de l'image (GHCR) via GitHub Actions
- [ ] **Vérification** : déploiement à partir du seul `docker-compose.yml` + `.env`

### Phase 9 — Qualité & documentation

- [ ] Tests unitaires des providers (clients HTTP simulés)
- [ ] Test d'intégration `docker compose up` → `/health` en CI
- [ ] README complet : installation, variables, exemples d'appels API
- [ ] Exemple d'intégration n8n (nœud HTTP Request vers `/api/chat`)

---

## 4. Variables d'environnement prévues

| Variable | Défaut | Rôle |
|---|---|---|
| `LARBINUS_PORT` | `8080` | Port exposé |
| `LARBINUS_API_KEY` | *(vide)* | Si renseignée, exige `X-API-Key` |
| `OLLAMA_BASE_URL` | *(vide)* | Ex. `http://192.168.x.x:11434` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | *(vide)* / `https://api.openai.com/v1` | OpenAI et compatibles |
| `ANTHROPIC_API_KEY` | *(vide)* | API Claude |
| `MISTRAL_API_KEY` | *(vide)* | API Mistral cloud |
| `DEFAULT_PROVIDER` / `DEFAULT_MODEL` | — | Sélection par défaut de l'UI |
| `DATA_DIR` | `/data` | Volume persistant (SQLite, index RAG) |

Un fournisseur sans clé (ou sans URL) est simplement absent de la liste : le conteneur
démarre et fonctionne même avec un seul fournisseur configuré.

---

## 5. Périmètre exclu (pour l'instant)

- Multi-utilisateurs avec comptes et permissions
- Appel d'outils / function calling
- Entrées images ou audio
- Interface mobile native

---

## 6. Questions ouvertes

- [ ] Nom d'hôte / sous-domaine souhaité derrière le reverse proxy
- [ ] Faut-il une limite de dépense ou un compteur de coût pour les API payantes ?
- [ ] Larbinus doit-il aussi savoir *piloter* Ollama (télécharger un modèle, le décharger) ?

---

## 7. Journal des bugs

> Une ligne par bug : symptôme, cause identifiée, correction, phase concernée.
> Objectif : ne jamais réintroduire un problème déjà résolu.

| # | Date | Phase | Symptôme | Cause | Correction | État |
|---|---|---|---|---|---|---|
| — | — | — | *(aucun bug enregistré à ce jour)* | — | — | — |

---

## 8. Journal des décisions et changements

| Date | Décision / changement | Motif |
|---|---|---|
| 2026-09-05 | Création du fichier de suivi, cadrage initial | Lancement du projet |
| 2026-09-05 | Python + FastAPI plutôt que Node | Écosystème IA, SDK officiels, RAG plus simple |
| 2026-09-05 | RAG repoussé en phase 7 | Livrer d'abord un chat multi-fournisseurs utilisable |
| 2026-09-05 | Ajout d'une API compatible OpenAI (`/v1/*`) dès la phase 3 | Utilisable tel quel par n8n, Open WebUI et tout client existant ; bien plus coûteux à greffer après coup |
| 2026-09-05 | Port interne figé à 8080, `LARBINUS_PORT` ne pilote que la publication sur le LAN | Évite la double source de vérité entre l'app, le `HEALTHCHECK` et le mapping compose |
| 2026-09-05 | Alias `host.docker.internal` déclaré dans le compose | Permet d'atteindre un Ollama installé sur l'hôte sans mode `network_host` |
| 2026-09-05 | Licence MIT, détenteur `mathgen44` | Projet ouvert et réutilisable ; le dépôt n'utilise que le pseudo, jamais le nom civil |
| 2026-09-05 | Aucune adresse d'Ollama codée en dur, uniquement des exemples commentés dans `.env.example` | Le projet doit rester déployable par d'autres utilisateurs avec leur propre infrastructure |
| 2026-09-05 | Port publié par défaut : 8080 → **8474** | 8080 déjà pris par SearXNG sur 192.168.0.40 ; 8474 est hors des plages encombrées et sous la plage éphémère du noyau (32768+). Le port interne du conteneur reste 8080, seul le mapping change |
| 2026-09-05 | Mistral implémenté comme sous-classe d'`OpenAICompatibleProvider` | Son API reprend le contrat OpenAI : une seule implémentation à maintenir |
| 2026-09-05 | `/health` ne contacte aucun fournisseur | Une API externe en panne ferait redémarrer le conteneur en boucle via le `HEALTHCHECK` ; le diagnostic des fournisseurs vit dans `/api/providers` |
| 2026-09-05 | `request.system` l'emporte sur un message de rôle `system` dans l'historique | Évite qu'un persona (phase 6) soit écrasé silencieusement par un historique importé |
| 2026-09-05 | Le préfixe n'est retiré que s'il correspond au fournisseur | Sinon `openai/mistralai/Mistral-7B` (OpenRouter) serait tronqué |
