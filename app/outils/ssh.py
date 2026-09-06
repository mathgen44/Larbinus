"""Exécution de commandes sur les machines déclarées, par SSH.

Deux garde-fous indépendants, parce qu'aucun ne suffit seul :

1. **L'inventaire.** Le modèle ne peut viser que des machines nommées dans
   `SSH_HOSTS`. Il ne peut pas inventer une adresse.
2. **La classification.** Une commande n'est exécutée sans confirmation que si
   elle figure dans une liste blanche de consultation *et* qu'elle ne contient
   aucun enchaînement shell. `df -h; rm -rf /` commence par une commande
   inoffensive : sans cette seconde condition, il partirait tout seul.

Le vrai rempart reste ailleurs : dans les droits de l'utilisateur SSH sur la
machine cible. Un compte dédié, une clé qui lui est propre, un `sudoers`
restreint. Si cette clé ouvre un shell root, rien ici ne rattrapera l'erreur.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
from dataclasses import dataclass

from app.outils.base import Niveau, Outil, Proposition, Resultat, tronquer

logger = logging.getLogger("larbinus.outils.ssh")


@dataclass
class Machine:
    nom: str
    utilisateur: str
    hote: str
    port: int = 22

    @property
    def cible(self) -> str:
        return f"{self.utilisateur}@{self.hote}"


def analyser_inventaire(declaration: str) -> dict[str, Machine]:
    """`beast=herve@192.168.0.139,vm=herve@192.168.0.40:2222` → inventaire."""
    machines: dict[str, Machine] = {}
    for entree in declaration.split(","):
        entree = entree.strip()
        if not entree or "=" not in entree:
            continue
        nom, _, adresse = entree.partition("=")
        if "@" not in adresse:
            logger.warning("Machine « %s » ignorée : format attendu nom=user@hote", nom)
            continue
        utilisateur, _, hote = adresse.partition("@")
        port = 22
        if ":" in hote:
            hote, _, port_texte = hote.partition(":")
            try:
                port = int(port_texte)
            except ValueError:
                logger.warning("Port illisible pour « %s », 22 retenu", nom)
        machines[nom.strip().lower()] = Machine(
            nom=nom.strip(), utilisateur=utilisateur.strip(), hote=hote.strip(), port=port
        )
    return machines


#: Commandes de consultation, par binaire puis sous-commande éventuelle.
#: Tout ce qui n'y figure pas est traité comme une écriture.
CONSULTATION: dict[str, set[str] | None] = {
    "docker": {"ps", "images", "logs", "inspect", "stats", "version", "info", "top"},
    "docker-compose": {"ps", "logs", "config", "version"},
    "systemctl": {"status", "list-units", "list-timers", "is-active", "is-enabled", "show"},
    "journalctl": None,
    "ollama": {"ps", "list", "show"},
    "df": None, "du": None, "free": None, "uptime": None, "uname": None,
    "hostname": None, "whoami": None, "id": None, "date": None,
    "ip": None, "ss": None, "netstat": None, "ping": None, "dig": None, "host": None,
    "ls": None, "cat": None, "head": None, "tail": None, "wc": None, "stat": None,
    "grep": None, "find": None, "file": None, "tree": None,
    "ps": None, "lsblk": None, "lscpu": None, "lsusb": None, "lspci": None,
    "nvidia-smi": None, "sensors": None, "zpool": {"status", "list", "iostat"},
    "zfs": {"list", "get"}, "pvesm": {"status", "list"}, "pvecm": {"status", "nodes"},
    "qm": {"list", "status", "config"}, "pct": {"list", "status", "config"},
    "git": {"status", "log", "diff", "remote", "branch"},
    "echo": None, "which": None, "type": None, "env": None, "printenv": None,
}

#: Caractères qui enchaînent, redirigent ou substituent une commande. Leur
#: présence suffit à exiger une confirmation, même sur une commande listée.
_ENCHAINEMENT = re.compile(r"[;&|><`$]|\$\(")


def classifier(commande: str) -> tuple[Niveau, str]:
    """Décide si une commande peut partir sans confirmation."""
    commande = commande.strip()
    if not commande:
        return Niveau.ECRITURE, "commande vide"

    if _ENCHAINEMENT.search(commande):
        return (
            Niveau.ECRITURE,
            "la commande enchaîne, redirige ou substitue — le début inoffensif "
            "d'une ligne ne dit rien de sa suite",
        )

    try:
        morceaux = shlex.split(commande)
    except ValueError as exc:
        return Niveau.ECRITURE, f"commande non analysable ({exc})"
    if not morceaux:
        return Niveau.ECRITURE, "commande vide"

    binaire = morceaux[0].rsplit("/", 1)[-1]
    if binaire == "sudo":
        return Niveau.ECRITURE, "élévation de privilèges"

    if binaire not in CONSULTATION:
        return Niveau.ECRITURE, f"« {binaire} » n'est pas une commande de consultation"

    sous_commandes = CONSULTATION[binaire]
    if sous_commandes is None:
        return Niveau.LECTURE, "commande de consultation"

    suivant = next((m for m in morceaux[1:] if not m.startswith("-")), None)
    if suivant is None:
        return Niveau.LECTURE, "commande de consultation"
    if suivant in sous_commandes:
        return Niveau.LECTURE, "commande de consultation"
    return Niveau.ECRITURE, f"« {binaire} {suivant} » n'est pas une consultation"


class OutilSSH(Outil):
    nom = "ssh"
    description = (
        "Exécuter une commande sur une machine du parc. Les commandes de "
        "consultation partent seules ; toute autre demande une confirmation."
    )
    exemple = (
        "```larbinus:ssh\n"
        "hote: <nom de la machine>\n"
        "commande: docker ps\n"
        "```"
    )

    def __init__(self, settings):
        self.settings = settings
        self.machines = analyser_inventaire(settings.ssh_hosts)

    @property
    def disponible(self) -> bool:
        return bool(self.machines)

    def preparer(self, parametres: dict[str, str], brut: str) -> Proposition:
        proposition = Proposition(outil=self.nom, parametres=parametres, brut=brut)

        hote = (parametres.get("hote") or parametres.get("machine") or "").strip().lower()
        commande = parametres.get("commande") or parametres.get("command") or ""
        proposition.resume = f"{hote or '?'} : {commande or '?'}"

        if not hote or not commande:
            proposition.erreur = (
                "Bloc incomplet : les clés « hote » et « commande » sont requises."
            )
            return proposition

        machine = self.machines.get(hote)
        if machine is None:
            connues = ", ".join(sorted(self.machines)) or "aucune"
            proposition.erreur = (
                f"Machine « {hote} » inconnue. Machines déclarées : {connues}."
            )
            return proposition

        proposition.parametres = {"hote": machine.nom, "commande": commande}
        proposition.resume = f"{machine.nom} : {commande}"
        proposition.niveau, proposition.motif = classifier(commande)
        return proposition

    def _arguments(self, machine: Machine, commande: str) -> list[str]:
        arguments = [
            "ssh",
            "-o", "BatchMode=yes",                 # jamais de demande de mot de passe
            "-o", f"ConnectTimeout={int(self.settings.ssh_connect_timeout)}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(machine.port),
        ]
        if self.settings.ssh_key_path:
            arguments += ["-i", self.settings.ssh_key_path]
        if self.settings.ssh_known_hosts:
            arguments += ["-o", f"UserKnownHostsFile={self.settings.ssh_known_hosts}"]
        arguments += [machine.cible, commande]
        return arguments

    async def executer(self, proposition: Proposition) -> Resultat:
        machine = self.machines[proposition.parametres["hote"].lower()]
        commande = proposition.parametres["commande"]
        debut = time.perf_counter()

        try:
            processus = await asyncio.create_subprocess_exec(
                *self._arguments(machine, commande),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return Resultat(
                outil=self.nom, resume=proposition.resume, succes=False,
                sortie="Le client ssh est absent de l'image du conteneur.",
            )

        try:
            sortie, erreur = await asyncio.wait_for(
                processus.communicate(), timeout=self.settings.ssh_timeout
            )
        except asyncio.TimeoutError:
            processus.kill()
            await processus.wait()
            return Resultat(
                outil=self.nom, resume=proposition.resume, succes=False,
                sortie=f"Commande interrompue après {self.settings.ssh_timeout} s.",
                duree_ms=round((time.perf_counter() - debut) * 1000),
            )

        texte = sortie.decode("utf-8", "replace")
        texte_erreur = erreur.decode("utf-8", "replace").strip()
        if texte_erreur:
            texte = f"{texte}\n[stderr] {texte_erreur}" if texte.strip() else texte_erreur

        texte, coupe = tronquer(texte.strip(), self.settings.tool_output_limit)
        return Resultat(
            outil=self.nom,
            resume=proposition.resume,
            sortie=texte,
            succes=processus.returncode == 0,
            code=processus.returncode,
            duree_ms=round((time.perf_counter() - debut) * 1000),
            tronque=coupe,
            meta={"hote": machine.nom},
        )
