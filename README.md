# Larbinus

**Mini assistant IA** — un petit conteneur Docker qui expose une interface de chat et une
API unifiée devant plusieurs modèles de langage : une instance **Ollama** locale et/ou les
API en ligne **OpenAI** (et compatibles), **Anthropic** et **Mistral AI**.

Larbinus expose également une API **compatible OpenAI** (`/v1/chat/completions`), ce qui le
rend utilisable tel quel depuis n8n, Open WebUI ou n'importe quel client existant.

> État : en construction. Voir [`suivi_de_projet.MD`](suivi_de_projet.MD) pour la feuille de
> route et l'avancement.

## Démarrage rapide

```bash
git clone https://github.com/mathgen44/Larbinus.git
cd Larbinus
cp .env.example .env      # puis renseigner au moins un fournisseur
docker compose up -d --build
```

Vérifier que le conteneur répond :

```bash
curl http://localhost:8474/health
# {"status":"ok","name":"Larbinus","version":"0.1.0","providers":["ollama"]}
```

Lister les modèles de tous les fournisseurs configurés :

```bash
curl http://localhost:8474/api/models
# [{"id":"ollama/mistral","name":"mistral","provider":"ollama","context_length":null}]
```

Diagnostiquer un fournisseur qui ne répond pas :

```bash
curl http://localhost:8474/api/providers
# [{"name":"ollama","available":false,"detail":"injoignable à l'adresse …","model_count":null}]
```

Dialoguer avec un modèle, en flux :

```bash
curl -N http://localhost:8474/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"ollama/mistral","messages":[{"role":"user","content":"Bonjour"}]}'
```

L'interface de chat est servie à la racine : **http://localhost:8474/**
(sélecteur de modèle, streaming, bloc « Raisonnement » repliable pour les modèles
qui en produisent, historique des conversations, thème clair/sombre, utilisable
au téléphone).

Les **larbins** sont des assistants préconfigurés (prompt système, modèle et
température par défaut). Quatre exemples sont fournis — Assistant, Développeur,
Homelab, Traducteur — modifiables et supprimables depuis l'interface. Démarrer une
conversation depuis un larbin copie ses réglages : le modifier ensuite ne réécrit
pas les conversations passées.

## Documents (RAG)

Larbinus peut répondre à partir de vos propres documents — PDF, Markdown, texte,
Word, Excel, HTML. Deux façons de les fournir :

* **glisser-déposer** dans l'écran « Documents » de l'interface ;
* **dossier surveillé** : tout fichier reconnu déposé dans `./documents`
  (monté sur `/documents` dans le conteneur, éventuellement un partage NFS ou
  Samba) est indexé au clic sur « Scanner le dossier surveillé ».

L'indexation demande un modèle d'embedding, à récupérer une fois :

```bash
ollama pull nomic-embed-text
```

Le fournisseur d'embeddings (`EMBEDDING_PROVIDER`) est indépendant du modèle de
conversation : on peut discuter avec une API en ligne tout en vectorisant ses
documents en local, ce qui évite de les envoyer chez un tiers.

Une fois des documents indexés, la case « Interroger mes documents » des
réglages active la recherche pour la conversation en cours. Les extraits
retenus s'affichent sous la réponse, avec leur fichier, leur section et leur
page — une réponse sourcée est vérifiable, une réponse sans source ne l'est pas.

Changer de modèle d'embedding rend l'index existant incomparable : Larbinus le
détecte et le signale. Il faut alors réinitialiser l'index
(`POST /api/documents/reinitialiser`) puis réindexer.

Les conversations sont enregistrées dans `data/larbinus.db` (SQLite) : elles
survivent au redémarrage du conteneur et peuvent être exportées en Markdown ou
en JSON depuis l'interface comme depuis l'API.

Documentation interactive de l'API : http://localhost:8474/docs

## API compatible OpenAI

Larbinus expose `/v1/chat/completions` et `/v1/models` au format OpenAI. N'importe
quel client de cet écosystème — n8n, Open WebUI, les SDK officiels — peut donc viser
Larbinus sans adaptateur, en choisissant le fournisseur par le nom du modèle :

```python
from openai import OpenAI

client = OpenAI(base_url="http://192.168.0.40:8474/v1", api_key="votre-cle-ou-nimporte-quoi")

reponse = client.chat.completions.create(
    model="ollama/mistral",                       # ou openai/gpt-4o-mini, anthropic/…
    messages=[{"role": "user", "content": "Bonjour"}],
    stream=True,
)
for fragment in reponse:
    print(fragment.choices[0].delta.content or "", end="")
```

Si `LARBINUS_API_KEY` est renseignée, elle est attendue dans `X-API-Key` **ou**
`Authorization: Bearer` — ce dernier étant ce qu'envoient les clients OpenAI.

## Configuration

Toute la configuration passe par le fichier `.env` (voir `.env.example`, entièrement
commenté). Principe : **un fournisseur sans clé — ou sans URL — est simplement désactivé**.
Le conteneur démarre et fonctionne même avec un seul fournisseur configuré.

| Variable | Rôle |
|---|---|
| `LARBINUS_PORT` | Port publié sur le LAN, `8474` par défaut (le port interne du conteneur reste `8080`) |
| `LARBINUS_API_KEY` | Si renseignée, exige `X-API-Key` ou `Authorization: Bearer` |
| `OLLAMA_BASE_URL` | Ex. `http://192.168.0.50:11434` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI et toute API compatible |
| `ANTHROPIC_API_KEY` | API Claude |
| `MISTRAL_API_KEY` | API Mistral cloud |
| `DATA_DIR` | Volume persistant (monté sur `./data`) |

Si Ollama tourne directement sur l'hôte Docker, utiliser
`OLLAMA_BASE_URL=http://host.docker.internal:11434` — l'alias est déjà déclaré dans
`docker-compose.yml`. Si Ollama tourne sur une autre machine du réseau, indiquer son URL
complète (`http://<ip-du-serveur>:11434`) : rien n'est codé en dur, chaque déploiement
pointe où il veut.

## Déploiement sur un serveur

```bash
git clone https://github.com/mathgen44/Larbinus.git /opt/larbinus
cd /opt/larbinus
cp .env.example .env && nano .env

# Le conteneur tourne en non-root (uid 1000) : le volume doit lui appartenir,
# sinon Docker le crée en root et l'application ne peut rien y écrire.
mkdir -p data && sudo chown -R 1000:1000 data

docker compose up -d --build
docker compose logs -f larbinus
curl http://localhost:8474/health
```

Mise à jour ultérieure :

```bash
cd /opt/larbinus && git pull && docker compose up -d --build
```

## Développement sans Docker

```bash
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate sous Windows
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
pytest
```

## Licence

[MIT](LICENSE).
