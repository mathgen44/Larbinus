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

## Outils des larbins

Un larbin peut demander l'exécution d'actions : consulter une machine par SSH,
lire un fichier accessible au conteneur. Il ne les exécute pas lui-même — il
les **propose** dans sa réponse, sous forme d'un bloc :

````
```larbinus:ssh
hote: beast
commande: docker ps
```
````

Larbinus lit ces blocs et applique une règle simple :

* une **commande de consultation** (`docker ps`, `systemctl status`,
  `journalctl`, `df`…) s'exécute seule, son résultat est transmis au modèle, et
  il poursuit son raisonnement ;
* **tout le reste attend votre accord** — y compris une commande de
  consultation qui enchaîne, redirige ou substitue. `df -h; rm -rf /` commence
  par une commande inoffensive : sans ce second contrôle, il partirait seul.

### Les autres outils

* **`fichier`** — lit un fichier ou liste un dossier parmi ceux auxquels le
  conteneur a accès. Toujours en lecture, donc sans confirmation.
* **`http`** — appelle l'API d'un service déclaré dans `HTTP_ALLOWED_HOSTS`.
  Souvent préférable à SSH : une API refuse ce qu'elle n'expose pas, là où un
  shell accepte tout. `GET` part seul, le reste attend votre accord.
* **`web`** — recherche en ligne via une instance **SearXNG**. Aucune clé, et
  aucune requête envoyée à un service tiers depuis Larbinus. SearXNG désactive
  le format JSON par défaut : ajoutez `json` à `search.formats` dans son
  `settings.yml`, sinon il répondra 403 — Larbinus vous le dira explicitement.

Les outils s'activent **larbin par larbin** (formulaire du larbin) ou
**conversation par conversation** (panneau des réglages). Aucun n'est actif par
défaut, et un outil non configuré n'apparaît pas.

### Le vrai rempart est côté serveur

Quoi que fasse Larbinus, ce qui délimite réellement ce qu'un modèle peut faire,
c'est ce que l'utilisateur SSH a le droit de faire. Créez un **compte dédié**
sur les machines cibles :

```bash
# Sur la machine cible
sudo adduser --disabled-password --gecos "" larbinus
sudo mkdir -p /home/larbinus/.ssh
sudo tee /home/larbinus/.ssh/authorized_keys < votre_cle.pub
sudo chown -R larbinus: /home/larbinus/.ssh
sudo chmod 700 /home/larbinus/.ssh && sudo chmod 600 /home/larbinus/.ssh/authorized_keys

# Puis, si des commandes privilégiées sont nécessaires, une liste explicite :
echo 'larbinus ALL=(ALL) NOPASSWD: /usr/bin/systemctl status *, /usr/bin/docker ps' \
  | sudo tee /etc/sudoers.d/larbinus
```

Sur la machine qui héberge Larbinus, générez une clé **dédiée** — jamais votre
clé personnelle :

```bash
mkdir -p ssh && ssh-keygen -t ed25519 -N "" -f ssh/id_ed25519 -C larbinus
ssh-keyscan -H 192.168.0.139 >> ssh/known_hosts
sudo chown -R 1000:1000 ssh && chmod 600 ssh/id_ed25519
```

Puis déclarez les machines dans `.env` :

```ini
SSH_HOSTS=beast=larbinus@192.168.0.139,vm=larbinus@192.168.0.40
SSH_KEY_PATH=/ssh/id_ed25519
```

Un larbin ne peut viser que les machines de cette liste. Les outils s'activent
larbin par larbin, ou conversation par conversation : aucun n'est actif par
défaut.

> **Un mot d'honnêteté sur les modèles locaux.** `mistral:7b` et
> `deepseek-r1:8b` se trompent de nom de conteneur, confondent deux machines,
> et n'ont aucune notion du caractère irréversible d'une commande. La
> confirmation n'est pas une formalité : lisez ce qu'ils proposent.

## Sécurité et exploitation

**Clé d'API.** `LARBINUS_API_KEY` protège les routes `/v1` — celles qu'appellent
n8n, les scripts et les SDK OpenAI. L'interface web (`/api`) reste accessible
sans clé : un navigateur ne peut pas en présenter une sans écran de saisie.
C'est confortable sur un LAN de confiance, mais **cela signifie que toute
machine du réseau peut lire vos documents indexés**. Dès que Larbinus dépasse ce
cadre, passez `LARBINUS_PROTECT_UI=true` : la clé est alors exigée partout.

**Limitation de débit.** `RATE_LIMIT_REQUESTS` requêtes par adresse et par
`RATE_LIMIT_WINDOW` secondes (120/60 par défaut, `0` désactive). Le but premier
est qu'un script parti en boucle ne vide pas un quota d'API payante. `/health`
et les fichiers statiques en sont exemptés — bloquer la sonde ferait redémarrer
le conteneur en boucle.

**Derrière un reverse proxy**, déclarez son adresse dans `TRUSTED_PROXIES` :

```ini
TRUSTED_PROXIES=172.18.0.1
```

Sans cela, `X-Forwarded-For` est ignoré et toutes les requêtes sont comptées
comme venant du proxy. L'en-tête n'est jamais cru d'une source non déclarée,
faute de quoi n'importe qui contournerait la limitation en le falsifiant.

Côté Nginx Proxy Manager, pensez à désactiver la mise en tampon pour que le
streaming fonctionne (Larbinus envoie déjà `X-Accel-Buffering: no`) et à porter
le délai de lecture au-delà du temps de génération le plus long :

```nginx
proxy_buffering off;
proxy_read_timeout 300s;
```

**Journaux.** `LOG_FORMAT=json` produit une ligne JSON par événement, avec
identifiant de requête, méthode, chemin, statut et durée — exploitable par un
collecteur. `LOG_FORMAT=texte` (défaut) reste lisible à l'œil. Chaque réponse
porte un en-tête `X-Request-ID` ; un identifiant fourni en amont est conservé,
ce qui permet de suivre une requête d'un service à l'autre.

## Déploiement depuis l'image publiée

Chaque poussée sur `main` construit l'image, la teste et la publie sur GHCR.
Le serveur n'a alors plus à compiler :

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

> **`denied` au premier `pull` ?** Les paquets GHCR sont **privés par défaut**,
> même quand le dépôt est public. Rendez-le public dans
> *Packages → larbinus → Package settings → Change visibility*, ou
> authentifiez-vous sur la machine avec un jeton personnel disposant de la
> portée `read:packages` :
>
> ```bash
> echo 'VOTRE_JETON' | docker login ghcr.io -u VOTRE_COMPTE --password-stdin
> ```

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
