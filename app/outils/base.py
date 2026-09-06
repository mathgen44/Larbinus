"""Socle des outils : protocole, classification, résultats.

Un larbin outillé ne fait rien lui-même : il **propose** une action dans sa
réponse, sous forme d'un bloc balisé. Larbinus lit ces blocs, décide s'ils
peuvent partir seuls, et n'exécute que ce qui est permis.

Le protocole choisi est volontairement rustique — un bloc de code avec des
lignes `clé: valeur` — plutôt que le mécanisme d'appel d'outils des grandes
API. Raison : les modèles locaux visés ici (`mistral:7b`, `deepseek-r1:8b`) ne
savent pas s'en servir, ou mal. Un format que n'importe quel modèle sait
produire vaut mieux qu'un format rigoureux que la moitié d'entre eux ignore.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("larbinus.outils")


class Niveau(str, Enum):
    """Ce que coûte une action si le modèle se trompe."""

    #: Consultation sans effet de bord : peut partir sans demander.
    LECTURE = "lecture"
    #: Modifie quelque chose, ou pourrait le faire : exige une confirmation.
    ECRITURE = "ecriture"


@dataclass
class Proposition:
    """Une action demandée par le modèle, pas encore exécutée."""

    outil: str
    parametres: dict[str, str]
    brut: str                      # le bloc tel qu'écrit par le modèle
    niveau: Niveau = Niveau.ECRITURE
    resume: str = ""               # description courte, affichée à l'utilisateur
    motif: str = ""                # pourquoi ce niveau a été retenu
    erreur: str | None = None      # proposition invalide : rien ne sera exécuté

    def to_dict(self) -> dict:
        return {
            "outil": self.outil,
            "parametres": self.parametres,
            "niveau": self.niveau.value,
            "resume": self.resume,
            "motif": self.motif,
            "erreur": self.erreur,
        }


@dataclass
class Resultat:
    """L'issue d'une exécution, telle qu'elle sera montrée et renvoyée au modèle."""

    outil: str
    resume: str
    sortie: str
    succes: bool = True
    code: int | None = None
    duree_ms: int = 0
    tronque: bool = False
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "outil": self.outil,
            "resume": self.resume,
            "sortie": self.sortie,
            "succes": self.succes,
            "code": self.code,
            "duree_ms": self.duree_ms,
            "tronque": self.tronque,
            **self.meta,
        }

    def pour_le_modele(self) -> str:
        """Formulation renvoyée au modèle pour qu'il poursuive son raisonnement."""
        entete = f"Résultat de l'outil « {self.outil} » ({self.resume})"
        if not self.succes:
            entete += " — ÉCHEC"
        if self.code is not None:
            entete += f", code de sortie {self.code}"
        corps = self.sortie or "(aucune sortie)"
        if self.tronque:
            corps += "\n[…] sortie tronquée."
        return f"{entete} :\n\n{corps}"


class Outil(ABC):
    """Contrat d'un outil.

    `preparer` valide une proposition et décide de son niveau ; `executer` la
    réalise. Séparer les deux permet de montrer à l'utilisateur ce qui va se
    passer **avant** que quoi que ce soit ne se passe.
    """

    nom: str = "base"
    description: str = ""
    #: Ce que le prompt système montre au modèle pour qu'il sache s'en servir.
    exemple: str = ""

    @abstractmethod
    def preparer(self, parametres: dict[str, str], brut: str) -> Proposition:
        ...

    @abstractmethod
    async def executer(self, proposition: Proposition) -> Resultat:
        ...

    async def aclose(self) -> None:
        return None


def tronquer(texte: str, limite: int) -> tuple[str, bool]:
    """Coupe une sortie trop longue.

    Le début **et** la fin sont conservés : sur un journal, l'information utile
    est presque toujours à l'une des deux extrémités, jamais au milieu.
    """
    if len(texte) <= limite:
        return texte, False
    tete = limite * 2 // 3
    queue = limite - tete
    return f"{texte[:tete]}\n\n[…]\n\n{texte[-queue:]}", True
