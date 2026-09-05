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
curl http://localhost:8080/health
# {"status":"ok","name":"Larbinus","version":"0.1.0","providers":["ollama"]}
```

Documentation interactive de l'API : http://localhost:8080/docs

## Configuration

Toute la configuration passe par le fichier `.env` (voir `.env.example`, entièrement
commenté). Principe : **un fournisseur sans clé — ou sans URL — est simplement désactivé**.
Le conteneur démarre et fonctionne même avec un seul fournisseur configuré.

| Variable | Rôle |
|---|---|
| `LARBINUS_PORT` | Port publié sur le LAN (le port interne reste `8080`) |
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
curl http://localhost:8080/health
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
