# Larbinus — Suivi de projet

> **Objectif** : un petit conteneur Docker, déployable via `docker compose`, capable
> d'interfacer des modèles d'IA en ligne et/ou une instance locale (Ollama), avec une
> interface de chat web et une API réutilisable dans le homelab.

| | |
|---|---|
| **Dépôt** | https://github.com/mathgen44/Larbinus |
| **Dossier de travail** | `D:\GIT\Larbinus` |
| **Machine de déploiement** | `192.168.0.40` — accès prévu sur `http://192.168.0.40:8474` |
| **Phase en cours** | Phase 8 terminée (à valider en réel) → Phase 9 (qualité, documentation) |
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
| Documents RAG | PDF, Markdown/texte, Word, Excel, HTML | 2026-09-05 |
| Ingestion | Dépôt dans l'interface **et** dossier surveillé monté dans le conteneur | 2026-09-05 |
| Stockage vectoriel | SQLite + `sqlite-vec`, avec repli en Python | 2026-09-05 |
| Portée de la clé d'API | `/v1` seulement ; l'interface reste libre sur le LAN (`LARBINUS_PROTECT_UI` pour tout fermer) | 2026-09-05 |

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
- [x] **Vérification en réel** : interface utilisée sur `http://192.168.0.40:8474/`

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
- [x] **Vérification en réel** : historique conservé dans `./data/larbinus.db`
      d'un redéploiement à l'autre

### Phase 6 — Personas (« larbins ») ✅ *terminée (validée par tests + navigateur)*

- [x] **Migration de schéma** v1 → v2 rejouable : table `personas`, colonnes
      `persona_id` et `temperature` sur `conversations`, pilotée par `PRAGMA user_version`
- [x] Table `personas` : nom, icône, description, prompt système, modèle et
      température par défaut
- [x] CRUD complet `GET/POST /api/personas`, `GET/PATCH/DELETE /api/personas/{id}`
- [x] Quatre personas d'exemple insérés **une seule fois** à la migration :
      Assistant, Développeur, Homelab, Traducteur. Supprimés, ils ne reviennent pas
- [x] Créer une conversation avec `persona_id` **copie** ses réglages dans la
      conversation ; ce que la requête précise l'emporte sur le persona
- [x] Le titre reste celui par défaut malgré le persona, pour être remplacé par la
      première question — une liste de quatre « Développeur » serait inutilisable
- [x] Supprimer un persona ne touche pas aux conversations : seul le lien est coupé
- [x] Interface : cartes de larbins sur l'écran d'accueil, dont une carte « + »
      en fin de grille qui ouvre directement le formulaire de création ; entrée
      « + Nouveau larbin » également en bas de la liste de la modale ; le bouton
      « Supprimer » est masqué tant qu'aucun larbin n'est sélectionné
- [x] Palette de 24 emojis cliquables pour l'icône du larbin, la saisie libre
      restant possible ; recliquer l'icône retenue la retire
- [x] 57 tests au vert, dont un qui **reconstitue une base v1 avec des données** et
      vérifie que la migration ne perd rien et ne duplique pas les exemples
- [x] **Vérification navigateur** : choix d'un larbin, prompt et température
      appliqués, conversation créée avec le bon persona, CRUD dans la modale,
      aucune erreur console
- [x] **Vérification en réel** : migrations v2, v3 et v4 appliquées en production
      sur la base existante, sans perte

### Phase 7a — Socle documentaire ✅ *terminée (validée par tests)*

- [x] **Migration v3** : tables `documents`, `chunks`, `rag_meta`, colonne `rag`
      sur `conversations` — appliquée à la suite des migrations existantes
- [x] Extraction par format : PDF (`pypdf`), Word (`python-docx`), Excel
      (`openpyxl`), HTML (`beautifulsoup4`, scripts et navigation retirés),
      Markdown et texte (découpage par titres)
- [x] Un PDF scanné produit un message explicite plutôt qu'un document vide
- [x] Découpage respectant les frontières de phrases, avec chevauchement ;
      les résidus trop courts sont rattachés au morceau précédent
- [x] Chaque fragment vectorisé est précédé de sa provenance
      (`[document — section — page]`), sans quoi un extrait isolé n'est
      rattachable à rien
- [x] Embeddings via Ollama (`/api/embed`, repli sur `/api/embeddings` pour les
      versions anciennes) ou API compatible OpenAI, par paquets de 32
- [x] Index `sqlite-vec` dans la base existante, **avec repli en similarité
      cosinus calculée en Python** si l'extension ne peut pas être chargée
- [x] **Confirmé en production** : `sqlite-vec` se charge bien dans l'image
      `python:3.12-slim` — `Index vectoriel : sqlite-vec, dimension 768`.
      Le repli n'a donc pas à servir, mais reste le filet de sécurité
- [x] Dimension et modèle d'embedding enregistrés : changer de modèle est
      détecté et signalé au lieu de produire des résultats faux
- [x] Empreinte SHA-256 unique : déposer deux fois le même fichier ne crée pas
      deux documents, quel que soit son nom
- [x] Dossier surveillé (`DOCUMENTS_DIR`, volume `./documents`) parcouru
      récursivement ; un scan ne réindexe que ce qui a changé
- [x] API : `GET/POST /api/documents`, `POST /api/documents/scan`,
      `POST /{id}/reindexer`, `DELETE /{id}`, `POST /reinitialiser`,
      `GET /api/documents/recherche`
- [x] 77 tests au vert, dont un qui rejoue la recherche **sans** `sqlite-vec`
      pour vérifier que le filet de sécurité donne les mêmes résultats

### Phase 7b — Intégration au chat et interface ✅ *terminée (tests + navigateur)*

- [x] **Migration v4** : colonne `sources` sur `messages` — rouvrir une
      conversation redonne les extraits d'origine, sans relancer une recherche
      qui en produirait d'autres
- [x] Le contexte est injecté dans le **prompt système du tour**, jamais dans
      l'historique enregistré : les extraits ne s'accumulent pas d'un tour à l'autre
- [x] Événement SSE `sources` émis **avant** la réponse : on voit sur quoi le
      modèle s'appuie pendant qu'il rédige
- [x] Bascule « Interroger mes documents » par conversation, avec le nombre de
      documents indexés en regard ; désactivée si aucun service d'embeddings
- [x] Le champ `rag` de la requête prime sur le réglage de la conversation :
      activation ponctuelle possible depuis l'API
- [x] Une recherche impossible (embeddings en panne, index incohérent) dégrade
      la réponse au lieu de la bloquer — le modèle répond sans documents
- [x] Sources affichées sous la réponse dans un bloc repliable : numéro,
      fichier, section, page et extrait
- [x] Écran « Documents » : glisser-déposer, liste avec état et fragments,
      réindexation, suppression, bouton de scan du dossier surveillé
- [x] Code et configuration indexables (`.py`, `.yml`, `.json`, `.sh`,
      `Dockerfile`, `Makefile`…) avec un lecteur distinct : ni détection de
      titres sur les `#`, ni normalisation des espaces, et un découpage par
      lignes entières
- [x] Fichiers déposés conservés sous `DATA_DIR/depots/` : une indexation
      échouée peut être reprise sans redéposer
- [x] Bouton « Réessayer les échecs » — le cas d'usage étant précisément
      « le modèle d'embedding manquait, je viens de l'installer »
- [x] 90 tests au vert, dont un qui vérifie **ce que le modèle reçoit
      réellement** dans son prompt système, et non seulement ce qu'il répond
- [x] **Vérification navigateur** : dépôt d'un fichier, scan du dossier,
      activation de la bascule, question dont la réponse n'existe que dans les
      documents, sources affichées avant la fin de la réponse, réglage et
      sources retrouvés après rechargement — aucune erreur console
- [x] **Vérification en réel** : `nomic-embed-text` installé sur 192.168.0.139,
      documents déposés et indexés depuis l'interface — validé par Hervé

### Phase 8 — Sécurité & mise en production ✅ *terminée (tests + serveur réel)*

- [x] `LARBINUS_API_KEY` exigée sur `/v1` (n8n, scripts, SDK OpenAI) ;
      l'interface `/api` reste libre — **choix assumé d'un LAN de confiance**
- [x] `LARBINUS_PROTECT_UI=true` étend l'exigence à l'interface, pour le jour
      où Larbinus sortira du réseau local
- [x] `/health` et `/` restent publics quoi qu'il arrive : la sonde Docker ne
      doit jamais dépendre d'une clé
- [x] Limitation de débit glissante par adresse, en mémoire (pas de Redis pour
      un conteneur unique) ; `/health` et les fichiers statiques exemptés
- [x] `X-Forwarded-For` n'est cru que d'un proxy déclaré dans `TRUSTED_PROXIES` —
      sinon la limitation se contourne en falsifiant un en-tête
- [x] En-têtes `Retry-After`, `X-RateLimit-Limit` et `X-RateLimit-Remaining`
- [x] Identifiant de requête sur chaque réponse (`X-Request-ID`), conservé s'il
      vient d'un service amont ; journal d'accès avec méthode, statut et durée
- [x] `LOG_FORMAT=json` pour un journal exploitable par un collecteur
- [x] GitHub Actions : tests, construction, publication sur GHCR, **puis
      démarrage de l'image publiée pour vérifier qu'elle répond sur `/health`**
- [x] `docker-compose.ghcr.yml` pour déployer sans compiler sur le serveur
- [x] README : portée de la clé, réglages de proxy, `proxy_buffering off`
- [x] 101 tests au vert, vérifiés avec `pytest` **et** `python -m pytest` —
      les deux invocations ne peuplent pas le chemin d'import de la même façon
- [x] **Vérification sur serveur réel** : 5 appels passent puis 429 avec
      `Retry-After`, `/v1` refuse sans clé et accepte avec, `/api` reste
      ouverte, le rejet 429 porte bien un identifiant et une ligne de journal
- [ ] **Vérification en réel** : premier `docker compose pull` depuis GHCR
      après la première poussée sur `main`

> **Piège GHCR** : un paquet fraîchement publié est **privé par défaut**, même
> si le dépôt est public — d'où un `denied` au premier `docker pull` anonyme.
> Le rendre public dans les réglages du paquet, ou `docker login ghcr.io` avec
> un jeton `read:packages`. Documenté dans le README.

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
| 6 | 2026-09-05 | 7 | `Ollama a répondu 404 : model "nomic-embed-text" not found` au dépôt d'un document | Le modèle d'embedding n'était pas installé sur l'instance Ollama | `ollama pull nomic-embed-text`. Message d'erreur déjà explicite, conservé tel quel | Clos |
| 7 | 2026-09-05 | 7 | Un document déposé dont l'indexation échoue ne pouvait pas être réessayé | Les octets du fichier n'étaient pas conservés : seul le dossier surveillé était relisible | Les fichiers déposés sont rangés sous `DATA_DIR/depots/<empreinte>` ; réindexation unitaire et bouton « Réessayer les échecs » ajoutés ; le fichier est supprimé avec le document | Corrigé |
| 8 | 2026-09-05 | 7 | Deux tests de recherche échouaient une fois sur deux | Le double de test vectorisait avec `hash()`, randomisé à chaque processus : le classement des résultats variait d'une exécution à l'autre | Remplacé par `zlib.crc32`, déterministe ; suite relancée trois fois pour le vérifier | Corrigé |
| 9 | 2026-09-05 | 8 | Une requête rejetée en 429 repartait sans identifiant ni ligne de journal d'accès | Ordre des intergiciels inversé : dans Starlette le **dernier** ajouté est le plus extérieur, or la limitation de débit avait été ajoutée en dernier | `ContexteRequete` ajouté après `LimitationDebit`, donc au-dessus ; test dédié reproduisant l'ordre de `main.py` | Corrigé |
| 10 | 2026-09-06 | 8 | CI en échec, `pytest` code de sortie 4, alors que la suite passe en local | Le script console `pytest` n'ajoute pas le répertoire courant au chemin d'import, contrairement à `python -m pytest` utilisé en local : `tests/conftest.py` ne trouvait pas le paquet `app`, et un échec d'import de conftest est traité comme une erreur d'usage (code 4) | `pyproject.toml` avec `pythonpath = ["."]`, et `python -m pytest` dans le workflow. Vérifié avec **les deux** formes d'invocation | Corrigé |
| 11 | 2026-09-06 | 8 | `docker pull` refusé : `denied` sur `ghcr.io/mathgen44/larbinus` | Un paquet GHCR fraîchement publié est **privé par défaut**, même quand le dépôt est public | Rendre le paquet public dans ses réglages, ou `docker login ghcr.io` avec un jeton `read:packages`. Documenté dans le README | Clos |

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
| 2026-09-05 | Les réglages d'un persona sont **copiés** dans la conversation, jamais référencés | Modifier un larbin ne doit pas réécrire rétroactivement des conversations dont les réponses ont été produites avec l'ancienne consigne |
| 2026-09-05 | Migrations pilotées par `PRAGMA user_version`, chacune rejouable | Une instance déployée ne doit jamais avoir à repartir d'une base vide ; les erreurs « duplicate column » sont ignorées |
| 2026-09-05 | Les personas d'exemple ne sont insérés qu'à la migration | S'ils étaient réinsérés au démarrage, ceux que l'utilisateur supprime reviendraient à chaque redémarrage |
| 2026-09-05 | La création d'un larbin se déclenche depuis la grille de choix (carte « + ») | C'est en parcourant les larbins existants qu'on constate qu'il en manque un ; obliger à passer par un écran de gestion séparé ajoute une étape inutile |
| 2026-09-05 | Le bouton « Supprimer » disparaît en mode création au lieu de changer de libellé | Un bouton qui change de rôle selon un état invisible est une source d'erreur ; mieux vaut qu'il ne soit pas là |
| 2026-09-05 | Palette d'emojis cliquables plutôt qu'un simple champ texte pour l'icône | Un champ vide n'indique pas qu'il attend un emoji ; question posée par l'utilisateur, donc défaut d'interface avéré. Les emojis proposés sont choisis parmi les symboles largement présents dans les polices système |
| 2026-09-05 | `sqlite-vec` dans la base existante, avec repli en Python | Garde un conteneur unique et un seul volume ; le repli évite qu'un build de Python sans support des extensions rende tout le RAG inutilisable |
| 2026-09-05 | Le fournisseur d'embeddings est indépendant du modèle de conversation | Permet de discuter avec une API en ligne tout en vectorisant en local — les documents ne partent pas chez un tiers |
| 2026-09-05 | La provenance est intégrée au texte vectorisé, pas seulement stockée à côté | Un fragment parlant de « la version 3 » ne se rattache à rien sans son en-tête ; avec le nom du document et la section, il devient retrouvable |
| 2026-09-05 | Dimension et modèle d'embedding verrouillés à la première indexation | Des vecteurs produits par deux modèles différents ne sont pas comparables : mieux vaut refuser que renvoyer des résultats silencieusement faux |
| 2026-09-05 | Une erreur d'extraction est enregistrée sur le document, jamais propagée | Un fichier illisible au milieu d'un dossier ne doit pas interrompre le traitement des autres |
| 2026-09-05 | Déduplication par empreinte SHA-256 | Le même fichier déposé sous deux noms, ou présent à la fois dans le dossier et en dépôt manuel, ne doit pas être indexé deux fois |
| 2026-09-05 | Le contexte documentaire va dans le prompt système du tour, pas dans l'historique | Sinon les extraits s'accumulent à chaque tour, gonflent le contexte et finissent par le saturer — critique avec 8 Go de VRAM |
| 2026-09-05 | Les sources sont émises avant la réponse et enregistrées avec elle | Voir les sources pendant la rédaction permet de juger la réponse au fil de l'eau ; les enregistrer permet de les revoir sans relancer une recherche qui donnerait d'autres extraits |
| 2026-09-05 | Une recherche documentaire en panne n'interrompt pas la conversation | Un service d'embeddings arrêté doit dégrader la réponse, pas empêcher de discuter |
| 2026-09-05 | Le code a son propre lecteur, distinct du Markdown | Un `#` en Python ou en YAML est un commentaire, pas un titre ; et l'indentation porte la structure, la normaliser rendrait les extraits inutilisables |
| 2026-09-05 | Les fichiers déposés sont conservés sur disque | Sans eux, un échec d'indexation est irrattrapable : l'utilisateur doit tout redéposer, alors que la panne était passagère |
| 2026-09-05 | La clé d'API ne garde que `/v1`, l'interface reste libre | Choix de Hervé : le LAN est de confiance, et un navigateur ne peut pas porter de clé sans écran de saisie. Le risque — documents indexés lisibles par toute machine du réseau — est explicite dans le README, et `LARBINUS_PROTECT_UI` permet de fermer |
| 2026-09-05 | Limitation de débit en mémoire, sans Redis | Un conteneur unique : le compteur repart à zéro au redémarrage, sans conséquence pour l'objectif visé |
| 2026-09-05 | `X-Forwarded-For` n'est cru que d'un proxy déclaré | Un en-tête librement falsifiable transformerait la limitation en simple décoration |
| 2026-09-05 | Le workflow GHCR démarre l'image publiée avant de la déclarer bonne | Une image qui se construit n'est pas une image qui fonctionne ; le `/health` le prouve |
| 2026-09-06 | `pythonpath = ["."]` dans `pyproject.toml` **et** `python -m pytest` en CI | Deux verrous indépendants : le premier rend la suite lançable de n'importe quelle façon, le second garantit que la CI reproduit l'invocation locale |
| 2026-09-06 | L'étiquette vérifiée par la CI est reprise de `metadata-action`, non recalculée | `git rev-parse --short` n'abrège pas toujours sur 7 caractères : l'étape aurait fini par chercher une image inexistante |
