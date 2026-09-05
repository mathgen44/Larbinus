# Larbinus — Suivi de projet

> **Objectif** : un petit conteneur Docker, déployable via `docker compose`, capable
> d'interfacer des modèles d'IA en ligne et/ou une instance locale (Ollama), avec une
> interface de chat web et une API réutilisable dans le homelab.

| | |
|---|---|
| **Dépôt** | https://github.com/mathgen44/Larbinus |
| **Dossier de travail** | `D:\GIT\Larbinus` |
| **Machine de déploiement** | `192.168.0.40` — accès prévu sur `http://192.168.0.40:8474` |
| **Phase en cours** | Phase 5 terminée (à valider en réel) → Phase 6 |
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
- [x] **Vérification en réel** : conteneur déployé sur 192.168.0.40:8474, `/api/models`
      renvoie bien les modèles de l'instance Ollama de 192.168.0.139

### Phase 3 — API de chat ✅ *terminée (validée par tests + serveur réel simulé)*

- [x] `POST /api/chat` : `{model, messages, system, temperature, max_tokens, stream}`
- [x] Streaming SSE événementiel (`delta` / `done` / `error`), avec arrêt sur
      déconnexion du client (`request.is_disconnected()`)
- [x] Comptage des jetons et `duration_ms` dans l'événement `done`
- [x] Mode non-streaming (`stream: false`) renvoyant la réponse complète en JSON
- [x] Erreurs normalisées : gestionnaire global `ProviderError` → code HTTP cohérent
      (503 / 401 / 404 / 429 / 400) ; une fois le flux commencé, l'erreur passe par
      un événement SSE `error` puisque les en-têtes sont déjà partis
- [x] **Couche compatible OpenAI** : `POST /v1/chat/completions` (fragments
      `chat.completion.chunk` + `data: [DONE]`) et `GET /v1/models`
- [x] Champs OpenAI inconnus (`top_p`, `presence_penalty`, `user`…) ignorés sans erreur
- [x] `app/security.py` : `X-API-Key` **ou** `Authorization: Bearer`, comparaison à
      temps constant ; `/health` reste public pour le `HEALTHCHECK` Docker
- [x] En-tête `X-Accel-Buffering: no` pour empêcher un reverse proxy Nginx de
      mettre le flux en tampon
- [x] **Modèles de raisonnement** : `reasoning` séparé de `delta` dans `ChatChunk`,
      lu depuis `message.thinking` (Ollama), `thinking_delta` (Anthropic) et
      `reasoning_content` / `reasoning` (DeepSeek, OpenRouter) ; exposé en événement
      SSE `reasoning`, en champ `reasoning` (non-streaming) et en `reasoning_content`
      dans la couche OpenAI
- [x] 35 tests au vert
- [x] **Vérification** : serveur réel lancé contre un faux Ollama qui répond en 250 ms
      par mot — fragments reçus au fil de l'eau (+116, +365, +615 ms…), pas d'un bloc
- [x] **Vérification** : le SDK officiel `openai` (celui qu'utilise n8n) dialogue avec
      Larbinus sans adaptateur, en streaming comme en non-streaming
- [x] **Vérification en réel** : `curl -N` sur `/api/chat` contre l'Ollama réel —
      réponse en flux, événement `done` avec compteurs de jetons et `duration_ms`
- [ ] **Vérification en réel** : les trois fournisseurs en ligne (clés non encore renseignées)

### Phase 4 — Interface web ✅ *terminée (validée au navigateur, reste à confirmer en réel)*

- [x] Page de chat statique, sans étape de build ni dépendance externe
      (`app/static/{index.html,styles.css,app.js}`), servie à la racine
- [x] Sélecteur fournisseur/modèle alimenté par `/api/models`, groupé par fournisseur,
      dernier modèle choisi mémorisé
- [x] Rendu Markdown écrit à la main : titres, listes, citations, liens, code en ligne
      et blocs de code avec bouton copier. **Tout est échappé avant mise en forme** —
      le texte vient d'un modèle, il ne doit jamais pouvoir injecter de HTML
- [x] Bloc « Raisonnement » repliable, replié par défaut, alimenté par l'événement SSE
      `reasoning`
- [x] Thème clair / sombre / auto, mise en page responsive (testée en 390 px)
- [x] Curseur clignotant pendant la génération, bouton « stop » (`AbortController`) qui
      conserve le texte déjà reçu
- [x] Panneau de réglages : prompt système et température
- [x] Ligne de statistiques par réponse : modèle, jetons, durée, débit en jetons/s
- [x] Rendu Markdown limité à une image par seconde (`requestAnimationFrame`) plutôt
      qu'à chaque fragment reçu
- [x] **Vérification** : parcours complet automatisé dans un vrai Chromium — envoi,
      streaming, raisonnement replié, liste, bloc de code, lien, bouton stop visible
      pendant la génération, aucune erreur console, en thème clair, sombre et mobile
- [ ] **Vérification en réel** : ouvrir `http://192.168.0.40:8474/` et converser avec
      `deepseek-r1:8b`

### Phase 5 — Persistance ✅ *terminée (validée par tests + navigateur)*

- [x] `app/storage/db.py` : SQLite en mode WAL, schéma `conversations` + `messages`
      avec suppression en cascade, `PRAGMA user_version` pour les migrations futures
- [x] `sqlite3` du module standard plutôt qu'un pilote asynchrone : chaque appel est
      dépaysé par `asyncio.to_thread`, les écritures sérialisées par un verrou
- [x] CRUD complet : `GET/POST /api/conversations`, `GET/PATCH/DELETE /api/conversations/{id}`
- [x] `conversation_id` optionnel sur `/api/chat` : **la base devient la source de
      vérité**, le serveur relit l'historique et n'attend du client que le message
      du tour. Sans ce champ, comportement inchangé (mode sans état)
- [x] Titre déduit automatiquement de la première question
- [x] Réponse partielle enregistrée même si la génération est interrompue
- [x] Export Markdown (raisonnement replié dans un `<details>`) et JSON, avec un nom
      de fichier lisible dérivé du titre
- [x] Interface : panneau latéral des conversations, reprise d'un fil, renommage en
      place, suppression, bouton d'export ; tiroir superposé en dessous de 820 px
- [x] 47 tests au vert
- [x] **Vérification navigateur** : deux messages, rechargement complet de la page,
      reprise du fil (4 messages et 2 blocs de raisonnement retrouvés), renommage,
      suppression, tiroir mobile — aucune erreur console
- [ ] **Vérification en réel** : redémarrer le conteneur et vérifier que l'historique
      survit dans `./data/larbinus.db`

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

## 4 bis. Points d'entrée de l'API

| Route | Rôle |
|---|---|
| `GET /health` | Sonde Docker — publique, ne contacte aucun fournisseur |
| `GET /api/models` | Modèles agrégés, identifiés `fournisseur/modèle` |
| `GET /api/providers` | État détaillé de chaque fournisseur (diagnostic) |
| `POST /api/chat` | Conversation, SSE par défaut (`stream: false` pour du JSON) |
| `GET /v1/models` | Idem, au format OpenAI |
| `POST /v1/chat/completions` | Conversation au format OpenAI (n8n, Open WebUI, SDK) |
| `GET /docs` | Documentation interactive générée |

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
| 1 | 2026-09-05 | 3 | `{"detail":"Method Not Allowed"}` en ouvrant `/api/chat` dans un navigateur | **Faux bug.** `/api/chat` n'accepte que le POST ; la barre d'adresse d'un navigateur émet un GET → 405 | Aucune. Comportement attendu. Utiliser `/docs`, `/health` ou `/api/models` depuis un navigateur ; la page de chat de la phase 4 fera les POST | Clos |
| 2 | 2026-09-05 | 2 | Le raisonnement de `deepseek-r1:8b` était purement perdu | Le provider Ollama ne lisait que `message.content` ; Ollama renvoie le monologue interne dans un champ **`thinking`** distinct (confirmé sur l'instance réelle) | Champ `reasoning` ajouté à `ChatChunk` et propagé jusqu'à l'API ; même traitement pour `thinking_delta` (Anthropic) et `reasoning_content` (DeepSeek, OpenRouter). 7 tests dédiés | Corrigé |
| 3 | 2026-09-05 | 4 | Le panneau de réglages s'affichait alors qu'il portait l'attribut `hidden` | `.reglages { display: grid }` est une règle d'auteur : elle l'emporte sur le `[hidden] { display: none }` de la feuille du navigateur | Règle globale `[hidden] { display: none !important; }` ajoutée en tête de la feuille de style | Corrigé |
| 4 | 2026-09-05 | 5 | `DELETE /api/conversations/{id}` faisait échouer le démarrage : « Status code 204 must not have a response body » | L'annotation de retour `-> None` amène FastAPI à générer un modèle de réponse, interdit sur un 204 | `response_class=Response`, annotation retirée, `Response(status_code=204)` renvoyé explicitement | Corrigé |
| 5 | 2026-09-05 | 5 | Le tiroir mobile fermé restait atteignable au clavier et lu par les lecteurs d'écran | `transform: translateX(-100%)` sort l'élément de l'écran mais le laisse dans l'ordre de tabulation | `visibility: hidden` ajouté, avec une transition retardée pour ne pas couper l'animation | Corrigé |

### Points de vigilance observés en production

- **GPU et contexte** : l'instance Ollama de 192.168.0.139 tourne sur une RTX 3070
  (8 Go). Avec `CONTEXT 32768`, le cache KV réclame 3,2 Go — autant que le modèle —
  et seules 26 couches sur 33 tiennent en VRAM (`23%/77% CPU/GPU`). Réduire
  `OLLAMA_CONTEXT_LENGTH` (8192 → cache KV ~800 Mo) fait tenir le modèle entier.
- **Mesures** : 39 s pour 24 jetons à froid (chargement du modèle inclus), 5,2 s pour
  22 jetons à chaud (~4,2 jetons/s). Une 3070 en tout-GPU devrait plutôt donner
  40 à 60 jetons/s sur un 7B quantifié — marge de progression restante.
- **Deux modèles ne cohabitent pas** en 8 Go de VRAM : alterner entre `mistral` et
  `deepseek-r1:8b` provoque un déchargement/rechargement à chaque bascule.

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
| 2026-09-05 | Routes réparties en `app/routers/` (`models`, `chat`, `openai`) | `main.py` redevenait un fourre-tout ; la couche OpenAI doit rester isolable |
| 2026-09-05 | Une erreur survenant après le début du flux est émise en SSE, pas en code HTTP | Les en-têtes de la réponse sont déjà envoyés : renvoyer un 503 est impossible à ce stade |
| 2026-09-05 | En-tête `X-Accel-Buffering: no` sur les réponses SSE | Sans lui, un reverse proxy Nginx (déjà présent dans le homelab) met le flux en tampon et le streaming devient invisible |
| 2026-09-05 | Le premier fragment OpenAI porte `{"role": "assistant"}` | Certains clients, dont d'anciens nœuds n8n, l'attendent avant tout contenu |
| 2026-09-05 | Raisonnement et réponse transportés dans deux champs distincts | Les fusionner est irréversible côté client ; l'interface doit pouvoir replier le monologue interne sans le confondre avec la réponse |
| 2026-09-05 | Le champ `reasoning` est **absent** (et non vide) pour un modèle classique | Permet au client de distinguer « ce modèle ne raisonne pas » de « raisonnement vide » |
| 2026-09-05 | Dans la couche OpenAI, le raisonnement passe par `reasoning_content` | Champ hors contrat OpenAI mais convention de fait chez DeepSeek et OpenRouter ; les clients stricts l'ignorent sans dommage |
| 2026-09-05 | Rendu Markdown écrit à la main plutôt qu'une bibliothèque en CDN | Le conteneur doit rester utilisable sur un réseau sans accès à Internet ; un CDN injoignable casserait toute l'interface |
| 2026-09-05 | Le message de l'utilisateur est inséré en `textContent`, jamais interprété | Il n'a pas à être rendu comme du Markdown, et cela supprime une surface d'injection |
| 2026-09-05 | Interface servie avec `Cache-Control: no-cache` | Une mise à jour de l'image ne doit pas rester masquée par le cache du navigateur |
| 2026-09-05 | Thème affiché en toutes lettres (auto / clair / sombre) plutôt qu'en pictogramme | Le glyphe ◐ manque dans beaucoup de polices système — constaté à la capture d'écran |
| 2026-09-05 | `sqlite3` standard plutôt qu'`aiosqlite` | Quelques écritures par réponse : `asyncio.to_thread` suffit, et c'est une dépendance de moins à suivre |
| 2026-09-05 | Avec `conversation_id`, la base est la source de vérité et le client n'envoie que le message du tour | Interdit toute divergence entre l'état du client et celui du serveur, et allège la charge utile |
| 2026-09-05 | Le raisonnement est enregistré mais **exclu** de l'historique renvoyé au modèle | Il gonflerait le contexte à chaque tour sans rien apporter, et certains fournisseurs le refusent |
| 2026-09-05 | La conversation est créée au premier message, pas à l'ouverture de la page | Sinon la liste se remplit de conversations vides à chaque visite |
| 2026-09-05 | Une génération interrompue est enregistrée telle quelle | Un début de réponse reste utile ; le perdre punirait l'utilisateur d'avoir cliqué sur « stop » |
