"""Lecture de fichiers, à l'intérieur du conteneur seulement.

Toujours en lecture, et toujours sous un répertoire autorisé. Le chemin est
résolu avant vérification : sans cela, `../../etc/shadow` sortirait du
périmètre en passant par un dossier permis.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.outils.base import Niveau, Outil, Proposition, Resultat, tronquer

logger = logging.getLogger("larbinus.outils.fichiers")


class OutilFichier(Outil):
    nom = "fichier"
    description = (
        "Lire un fichier ou lister un dossier parmi ceux auxquels Larbinus a accès "
        "(documents et données)."
    )
    exemple = (
        "```larbinus:fichier\n"
        "chemin: /documents/procedure.md\n"
        "```"
    )

    def __init__(self, settings):
        self.settings = settings

    @property
    def racines(self) -> list[Path]:
        chemins = [Path(self.settings.documents_dir), Path(self.settings.data_dir)]
        return [c.resolve() for c in chemins if c.exists()]

    @property
    def disponible(self) -> bool:
        return bool(self.racines)

    def _autorise(self, chemin: Path) -> bool:
        return any(chemin == racine or racine in chemin.parents for racine in self.racines)

    def preparer(self, parametres: dict[str, str], brut: str) -> Proposition:
        proposition = Proposition(
            outil=self.nom, parametres=parametres, brut=brut,
            niveau=Niveau.LECTURE, motif="lecture seule",
        )

        brut_chemin = parametres.get("chemin") or parametres.get("path") or ""
        proposition.resume = brut_chemin or "?"
        if not brut_chemin:
            proposition.erreur = "Bloc incomplet : la clé « chemin » est requise."
            return proposition

        # `resolve()` avant contrôle : c'est ce qui neutralise les « .. ».
        chemin = Path(brut_chemin).resolve()
        if not self._autorise(chemin):
            autorisees = ", ".join(str(r) for r in self.racines) or "aucune"
            proposition.erreur = (
                f"Chemin hors périmètre. Répertoires accessibles : {autorisees}."
            )
            return proposition

        proposition.parametres = {"chemin": str(chemin)}
        proposition.resume = str(chemin)
        return proposition

    async def executer(self, proposition: Proposition) -> Resultat:
        chemin = Path(proposition.parametres["chemin"])
        debut = time.perf_counter()

        def lire() -> tuple[str, bool]:
            if chemin.is_dir():
                entrees = sorted(
                    f"{'dossier' if e.is_dir() else 'fichier':7} {e.name}"
                    for e in chemin.iterdir()
                )
                return "\n".join(entrees) or "(dossier vide)", True
            return chemin.read_text(encoding="utf-8", errors="replace"), False

        try:
            contenu, est_dossier = await asyncio.to_thread(lire)
        except FileNotFoundError:
            return Resultat(
                outil=self.nom, resume=proposition.resume, succes=False,
                sortie="Fichier ou dossier introuvable.",
            )
        except OSError as exc:
            return Resultat(
                outil=self.nom, resume=proposition.resume, succes=False,
                sortie=f"Lecture impossible : {exc}",
            )

        contenu, coupe = tronquer(contenu, self.settings.tool_output_limit)
        return Resultat(
            outil=self.nom,
            resume=proposition.resume,
            sortie=contenu,
            duree_ms=round((time.perf_counter() - debut) * 1000),
            tronque=coupe,
            meta={"dossier": est_dossier},
        )
