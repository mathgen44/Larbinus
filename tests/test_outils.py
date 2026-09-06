"""Moteur d'outils (phase 10a) — protocole, classification, exécution."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.outils.analyse import analyser_bloc, extraire_blocs, retirer_blocs
from app.outils.base import Niveau, Resultat, tronquer
from app.outils.registre import RegistreOutils
from app.outils.ssh import analyser_inventaire, classifier
from app.providers.registry import ProviderRegistry
from tests.conftest import ndjson, patch_provider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def reglages(tmp_path, **extra) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        documents_dir=str(tmp_path / "documents"),
        ssh_hosts="beast=herve@192.168.0.139,vm=herve@192.168.0.40:2222",
        **extra,
    )


# --------------------------------------------------------------------------- #
#  Analyse des blocs
# --------------------------------------------------------------------------- #
def test_extraction_d_un_bloc():
    reponse = """Je regarde les conteneurs.

```larbinus:ssh
hote: beast
commande: docker ps
```
"""
    blocs = extraire_blocs(reponse)
    assert len(blocs) == 1
    outil, parametres, _ = blocs[0]
    assert outil == "ssh"
    assert parametres == {"hote": "beast", "commande": "docker ps"}


def test_analyse_tolerante():
    """Un modèle de 7 milliards de paramètres respecte le format « à peu près »."""
    parametres = analyser_bloc(
        'Hôte :  beast \n'
        'Commande: "docker ps --format \'{{.Names}}: {{.Status}}\'"\n'
        'ligne sans separateur\n'
    )
    # Clé accentuée, majuscule et espace avant le deux-points : acceptées.
    assert parametres["hote"] == "beast"
    # La valeur est découpée au PREMIER deux-points, et déguillemetée.
    assert parametres["commande"] == "docker ps --format '{{.Names}}: {{.Status}}'"


def test_bloc_non_ferme_est_quand_meme_lu():
    """Une réponse coupée net ne doit pas faire perdre la proposition."""
    blocs = extraire_blocs("```larbinus:ssh\nhote: beast\ncommande: uptime\n")
    assert blocs and blocs[0][1]["commande"] == "uptime"


def test_retrait_des_blocs():
    reponse = "Avant.\n\n```larbinus:ssh\nhote: beast\ncommande: uptime\n```\n\nAprès."
    propre = retirer_blocs(reponse)
    assert "larbinus:ssh" not in propre
    assert "Avant." in propre and "Après." in propre


# --------------------------------------------------------------------------- #
#  Inventaire
# --------------------------------------------------------------------------- #
def test_inventaire():
    machines = analyser_inventaire(
        "beast=herve@192.168.0.139,vm=herve@192.168.0.40:2222,casse=,bidon"
    )
    assert set(machines) == {"beast", "vm"}
    assert machines["beast"].port == 22
    assert machines["vm"].port == 2222
    assert machines["vm"].cible == "herve@192.168.0.40"


# --------------------------------------------------------------------------- #
#  Classification lecture / écriture
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "commande",
    [
        "docker ps",
        "docker logs larbinus --tail 50",
        "systemctl status ollama",
        "journalctl -u ollama -n 100",
        "df -h",
        "nvidia-smi",
        "ollama ps",
        "cat /etc/hostname",
        "qm list",
    ],
)
def test_commandes_de_consultation(commande):
    niveau, _ = classifier(commande)
    assert niveau is Niveau.LECTURE, commande


@pytest.mark.parametrize(
    "commande",
    [
        "docker compose down -v",
        "docker rm -f larbinus",
        "systemctl stop ollama",
        "rm -rf /opt/larbinus",
        "sudo reboot",
        "apt upgrade -y",
        "qm stop 100",
    ],
)
def test_commandes_qui_exigent_confirmation(commande):
    niveau, motif = classifier(commande)
    assert niveau is Niveau.ECRITURE, commande
    assert motif


@pytest.mark.parametrize(
    "commande",
    [
        "df -h; rm -rf /",
        "docker ps && docker rm -f larbinus",
        "cat /etc/passwd > /tmp/vol",
        "echo $(rm -rf /tmp/x)",
        "uptime | tee /etc/motd",
        "ls `whoami`",
    ],
)
def test_un_enchainement_ne_part_jamais_seul(commande):
    """Le début inoffensif d'une ligne ne dit rien de sa suite.

    C'est le contrôle qui compte le plus : sans lui, il suffirait de faire
    précéder une commande destructrice d'un `df -h;` pour qu'elle s'exécute
    sans confirmation.
    """
    niveau, motif = classifier(commande)
    assert niveau is Niveau.ECRITURE, commande
    assert "enchaîne" in motif or "analysable" in motif


# --------------------------------------------------------------------------- #
#  Préparation des propositions
# --------------------------------------------------------------------------- #
def test_machine_hors_inventaire_refusee(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    propositions = registre.propositions(
        "```larbinus:ssh\nhote: routeur\ncommande: uptime\n```", ["ssh"]
    )
    assert propositions[0].erreur is not None
    assert "inconnue" in propositions[0].erreur
    assert registre.automatique(propositions[0]) is False


def test_outil_non_active_refuse(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    propositions = registre.propositions(
        "```larbinus:ssh\nhote: beast\ncommande: uptime\n```", []
    )
    assert "non activé" in propositions[0].erreur


def test_outil_inconnu_refuse(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    propositions = registre.propositions(
        "```larbinus:telepathie\nquestion: quoi ?\n```", ["ssh"]
    )
    assert "inconnu" in propositions[0].erreur


def test_bloc_incomplet_refuse(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    propositions = registre.propositions(
        "```larbinus:ssh\nhote: beast\n```", ["ssh"]
    )
    assert "incomplet" in propositions[0].erreur


def test_proposition_de_lecture_est_automatique(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    proposition = registre.propositions(
        "```larbinus:ssh\nhote: beast\ncommande: docker ps\n```", ["ssh"]
    )[0]
    assert proposition.niveau is Niveau.LECTURE
    assert registre.automatique(proposition) is True
    assert proposition.resume == "beast : docker ps"


# --------------------------------------------------------------------------- #
#  Outil fichier
# --------------------------------------------------------------------------- #
async def test_lecture_de_fichier(tmp_path):
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "note.md").write_text("Le port est 8474.")

    registre = RegistreOutils(reglages(tmp_path))
    proposition = registre.propositions(
        f"```larbinus:fichier\nchemin: {tmp_path}/documents/note.md\n```", ["fichier"]
    )[0]
    assert registre.automatique(proposition) is True

    resultat = await registre.executer(proposition)
    assert resultat.succes and "8474" in resultat.sortie


async def test_listage_d_un_dossier(tmp_path):
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "a.md").write_text("a")
    registre = RegistreOutils(reglages(tmp_path))
    proposition = registre.propositions(
        f"```larbinus:fichier\nchemin: {tmp_path}/documents\n```", ["fichier"]
    )[0]
    resultat = await registre.executer(proposition)
    assert "a.md" in resultat.sortie


def test_echappement_du_perimetre_refuse(tmp_path):
    """`..` ne doit pas permettre de sortir des répertoires autorisés."""
    (tmp_path / "documents").mkdir()
    registre = RegistreOutils(reglages(tmp_path))
    proposition = registre.propositions(
        f"```larbinus:fichier\nchemin: {tmp_path}/documents/../../../etc/passwd\n```",
        ["fichier"],
    )[0]
    assert proposition.erreur is not None
    assert "périmètre" in proposition.erreur


# --------------------------------------------------------------------------- #
#  Sortie tronquée
# --------------------------------------------------------------------------- #
def test_troncature_garde_le_debut_et_la_fin():
    texte = "DEBUT" + "x" * 5000 + "FIN"
    coupe, tronque = tronquer(texte, 200)
    assert tronque is True
    assert coupe.startswith("DEBUT")
    assert coupe.endswith("FIN")
    assert "[…]" in coupe


def test_resultat_formate_pour_le_modele():
    resultat = Resultat(
        outil="ssh", resume="beast : docker ps", sortie="CONTAINER ID",
        succes=False, code=1, tronque=True,
    )
    texte = resultat.pour_le_modele()
    assert "ÉCHEC" in texte
    assert "code de sortie 1" in texte
    assert "tronquée" in texte


# --------------------------------------------------------------------------- #
#  Consigne injectée au modèle
# --------------------------------------------------------------------------- #
def test_consigne_liste_les_machines(tmp_path):
    registre = RegistreOutils(reglages(tmp_path))
    consigne = registre.consigne(["ssh"])
    assert "larbinus:ssh" in consigne
    assert "beast" in consigne and "vm" in consigne
    assert "n'invente jamais" in consigne


def test_aucune_consigne_sans_outil(tmp_path):
    assert RegistreOutils(reglages(tmp_path)).consigne([]) == ""


def test_ssh_inactif_sans_inventaire(tmp_path):
    registre = RegistreOutils(
        Settings(_env_file=None, data_dir=str(tmp_path), ssh_hosts="")
    )
    assert "ssh" not in registre.noms


# --------------------------------------------------------------------------- #
#  Boucle de conversation
# --------------------------------------------------------------------------- #
@pytest.fixture
def client_outils(tmp_path):
    """Modèle simulé qui propose une lecture de fichier, puis conclut."""
    import app.main as principal

    principal.settings.data_dir = str(tmp_path)
    principal.settings.documents_dir = str(tmp_path / "documents")
    principal.settings.ssh_hosts = "beast=herve@192.168.0.139"
    (tmp_path / "documents").mkdir(exist_ok=True)
    (tmp_path / "documents" / "note.md").write_text("Le port retenu est 8474.")

    tours = iter(
        [
            ndjson(
                {"message": {"content": "Je consulte la note.\n\n```larbinus:fichier\n"
                                        f"chemin: {tmp_path}/documents/note.md\n```"},
                 "done": False},
                {"done": True, "prompt_eval_count": 10, "eval_count": 20},
            ),
            ndjson(
                {"message": {"content": "Le port retenu est 8474."}, "done": False},
                {"done": True, "prompt_eval_count": 40, "eval_count": 8},
            ),
        ]
    )

    def repondre(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "mistral"}]})
        return httpx.Response(200, content=next(tours, ndjson({"done": True})))

    with TestClient(app) as testeur:
        principal.settings.data_dir = str(tmp_path)
        app.state.settings = principal.settings
        app.state.outils = RegistreOutils(principal.settings)
        registry = ProviderRegistry(
            Settings(_env_file=None, ollama_base_url="http://ollama.test:11434")
        )
        registry.get("ollama")._client = httpx.AsyncClient(
            transport=httpx.MockTransport(repondre)
        )
        app.state.registry = registry
        yield testeur


def test_une_lecture_s_enchaine_seule(client_outils, tmp_path):
    conversation = client_outils.post(
        "/api/conversations", json={"tools": ["fichier"]}
    ).json()
    assert conversation["tools"] == '["fichier"]'

    reponse = client_outils.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": conversation["id"],
              "messages": [{"role": "user", "content": "Quel port ?"}], "stream": False},
    ).json()

    # L'outil a été exécuté, puis le modèle a conclu à partir du résultat.
    assert reponse["outils"][0]["outil"] == "fichier"
    assert "8474" in reponse["outils"][0]["sortie"]
    assert reponse["content"] == "Le port retenu est 8474."

    messages = client_outils.get(
        f"/api/conversations/{conversation['id']}"
    ).json()["messages"]
    genres = [(m["role"], m["kind"]) for m in messages]
    assert ("user", "outil") in genres
    # Le bloc d'action ne doit pas rester dans le contenu enregistré, sinon le
    # modèle le reproduirait au tour suivant.
    assert all("larbinus:fichier" not in m["content"] for m in messages)


def test_le_titre_ne_vient_jamais_d_un_compte_rendu(client_outils):
    conversation = client_outils.post(
        "/api/conversations", json={"tools": ["fichier"]}
    ).json()
    client_outils.post(
        "/api/chat",
        json={"model": "ollama/mistral", "conversation_id": conversation["id"],
              "messages": [{"role": "user", "content": "Quel port ?"}], "stream": False},
    )
    detail = client_outils.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["title"] == "Quel port ?"


def test_catalogue_des_outils(client_outils):
    catalogue = client_outils.get("/api/outils").json()["outils"]
    noms = {o["nom"] for o in catalogue}
    assert {"ssh", "fichier"} <= noms
    ssh = next(o for o in catalogue if o["nom"] == "ssh")
    assert ssh["machines"] == ["beast"]


def test_execution_confirmee_revalide(client_outils):
    """Confirmer une action ne dispense pas de vérifier son périmètre."""
    refus = client_outils.post(
        "/api/outils/executer",
        json={"outil": "ssh", "parametres": {"hote": "routeur", "commande": "reboot"}},
    )
    assert refus.status_code == 400
    assert "inconnue" in refus.json()["detail"]

    inconnu = client_outils.post(
        "/api/outils/executer", json={"outil": "telepathie", "parametres": {}}
    )
    assert inconnu.status_code == 404


# --------------------------------------------------------------------------- #
#  Outil HTTP
# --------------------------------------------------------------------------- #
def reglages_http(tmp_path, **extra) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        documents_dir=str(tmp_path / "documents"),
        http_allowed_hosts="portainer.lan:9000,192.168.0.40:8080",
        **extra,
    )


def test_hote_non_autorise_refuse(tmp_path):
    registre = RegistreOutils(reglages_http(tmp_path))
    proposition = registre.propositions(
        "```larbinus:http\nurl: https://api.exemple.com/v1/tout\n```", ["http"]
    )[0]
    assert "non autorisé" in proposition.erreur


def test_le_port_compte_dans_la_liste_blanche(tmp_path):
    """Un service autorisé sur 9000 ne l'est pas sur 22."""
    registre = RegistreOutils(reglages_http(tmp_path))
    proposition = registre.propositions(
        "```larbinus:http\nurl: http://portainer.lan:22/api\n```", ["http"]
    )[0]
    assert proposition.erreur is not None


def test_get_est_automatique_mais_pas_post(tmp_path):
    registre = RegistreOutils(reglages_http(tmp_path))

    lecture = registre.propositions(
        "```larbinus:http\nurl: http://portainer.lan:9000/api/endpoints\n```", ["http"]
    )[0]
    assert lecture.niveau is Niveau.LECTURE
    assert registre.automatique(lecture) is True

    ecriture = registre.propositions(
        "```larbinus:http\nurl: http://portainer.lan:9000/api/stacks\n"
        "methode: POST\ncorps: {}\n```",
        ["http"],
    )[0]
    assert ecriture.niveau is Niveau.ECRITURE
    assert registre.automatique(ecriture) is False


def test_schema_non_http_refuse(tmp_path):
    registre = RegistreOutils(reglages_http(tmp_path))
    proposition = registre.propositions(
        "```larbinus:http\nurl: file:///etc/passwd\n```", ["http"]
    )[0]
    assert proposition.erreur is not None


async def test_execution_http_reindente_le_json(tmp_path):
    registre = RegistreOutils(reglages_http(tmp_path))
    outil = registre.get("http")
    outil._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda requete: httpx.Response(
                200, json={"nom": "larbinus", "etat": "running"}
            )
        )
    )
    proposition = registre.propositions(
        "```larbinus:http\nurl: http://portainer.lan:9000/api/x\n```", ["http"]
    )[0]
    resultat = await registre.executer(proposition)
    assert resultat.succes and resultat.code == 200
    # Réindenté : une ligne unique de plusieurs milliers de caractères serait
    # illisible pour le modèle comme pour l'utilisateur.
    assert '"nom": "larbinus"' in resultat.sortie
    assert "\n" in resultat.sortie
    await outil.aclose()


# --------------------------------------------------------------------------- #
#  Outil web
# --------------------------------------------------------------------------- #
def reglages_web(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        documents_dir=str(tmp_path / "documents"),
        web_search_url="http://192.168.0.40:8080/search",
    )


async def test_recherche_web(tmp_path):
    registre = RegistreOutils(reglages_web(tmp_path))
    outil = registre.get("web")
    outil._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda requete: httpx.Response(
                200,
                json={"results": [
                    {"title": "Proxmox VE", "url": "https://pve.exemple",
                     "content": "Derniere version stable."},
                ]},
            )
        )
    )
    proposition = registre.propositions(
        "```larbinus:web\nrequete: version de proxmox\n```", ["web"]
    )[0]
    assert registre.automatique(proposition) is True

    resultat = await registre.executer(proposition)
    assert "[1] Proxmox VE" in resultat.sortie
    assert "https://pve.exemple" in resultat.sortie
    await outil.aclose()


async def test_searxng_sans_format_json_donne_la_marche_a_suivre(tmp_path):
    """Le 403 de SearXNG est très reconnaissable : autant expliquer d'emblée."""
    registre = RegistreOutils(reglages_web(tmp_path))
    outil = registre.get("web")
    outil._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda requete: httpx.Response(403, text=""))
    )
    proposition = registre.propositions(
        "```larbinus:web\nrequete: test\n```", ["web"]
    )[0]
    resultat = await registre.executer(proposition)
    assert resultat.succes is False
    assert "settings.yml" in resultat.sortie
    await outil.aclose()


def test_outils_inactifs_sans_configuration(tmp_path):
    registre = RegistreOutils(
        Settings(_env_file=None, data_dir=str(tmp_path), documents_dir=str(tmp_path))
    )
    assert "http" not in registre.noms
    assert "web" not in registre.noms
