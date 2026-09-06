"""Registre des outils : disponibilité, consigne au modèle, exécution.

C'est ici que se décide ce qui part seul et ce qui attend un clic. La règle
tient en une phrase : **une proposition n'est exécutée automatiquement que si
elle est classée en lecture et que son outil est activé pour la conversation.**
Tout le reste remonte à l'utilisateur.
"""

from __future__ import annotations

import logging

from app.outils.analyse import extraire_blocs
from app.outils.base import Niveau, Outil, Proposition, Resultat
from app.outils.fichiers import OutilFichier
from app.outils.http import OutilHTTP
from app.outils.ssh import OutilSSH
from app.outils.web import OutilWeb

logger = logging.getLogger("larbinus.outils")

CONSIGNE = """Tu peux demander l'exécution d'actions en écrivant un bloc dans ta réponse.

Règles :
- un bloc par action, et seulement quand c'est nécessaire pour répondre ;
- annonce en une phrase ce que tu cherches, puis écris le bloc ;
- arrête-toi après le bloc : le résultat te sera transmis, tu poursuivras ensuite ;
- n'invente jamais le résultat d'une action que tu n'as pas encore obtenu ;
- les commandes de consultation s'exécutent seules, les autres attendent
  l'accord de l'utilisateur — propose-les quand même, en expliquant pourquoi.

Outils à ta disposition :

{outils}"""


class RegistreOutils:
    def __init__(self, settings):
        self.settings = settings
        self._outils: dict[str, Outil] = {}

        ssh = OutilSSH(settings)
        if ssh.disponible:
            self._outils[ssh.nom] = ssh
        else:
            logger.info("Outil ssh inactif : SSH_HOSTS n'est pas renseigné.")

        fichier = OutilFichier(settings)
        if fichier.disponible:
            self._outils[fichier.nom] = fichier

        http = OutilHTTP(settings)
        if http.disponible:
            self._outils[http.nom] = http
        else:
            logger.info("Outil http inactif : HTTP_ALLOWED_HOSTS n'est pas renseigné.")

        web = OutilWeb(settings)
        if web.disponible:
            self._outils[web.nom] = web
        else:
            logger.info("Outil web inactif : WEB_SEARCH_URL n'est pas renseigné.")

    @property
    def noms(self) -> list[str]:
        return sorted(self._outils)

    def get(self, nom: str) -> Outil | None:
        return self._outils.get(nom)

    def catalogue(self) -> list[dict]:
        """Description des outils, pour l'interface."""
        catalogue = []
        for nom, outil in sorted(self._outils.items()):
            entree = {
                "nom": nom,
                "description": outil.description,
                "exemple": outil.exemple,
            }
            if isinstance(outil, OutilSSH):
                entree["machines"] = sorted(m.nom for m in outil.machines.values())
            if isinstance(outil, OutilHTTP):
                entree["services"] = sorted(outil.hotes)
            catalogue.append(entree)
        return catalogue

    def actifs(self, demandes: list[str] | None) -> list[str]:
        """Outils réellement utilisables : demandés, et effectivement présents."""
        if not demandes:
            return []
        return [nom for nom in demandes if nom in self._outils]

    def consigne(self, actifs: list[str]) -> str:
        """Bloc ajouté au prompt système pour apprendre le protocole au modèle."""
        if not actifs:
            return ""
        descriptions = []
        for nom in actifs:
            outil = self._outils[nom]
            details = f"**{nom}** — {outil.description}\n{outil.exemple}"
            if isinstance(outil, OutilSSH):
                machines = ", ".join(sorted(m.nom for m in outil.machines.values()))
                details += f"\nMachines déclarées : {machines}."
            if isinstance(outil, OutilHTTP):
                details += f"\nServices autorisés : {', '.join(sorted(outil.hotes))}."
            descriptions.append(details)
        return CONSIGNE.format(outils="\n\n".join(descriptions))

    # -- analyse et exécution ---------------------------------------------- #
    def propositions(self, reponse: str, actifs: list[str]) -> list[Proposition]:
        """Propositions valides tirées d'une réponse du modèle."""
        resultat: list[Proposition] = []
        for nom, parametres, brut in extraire_blocs(reponse):
            outil = self._outils.get(nom)
            if outil is None:
                resultat.append(
                    Proposition(
                        outil=nom, parametres=parametres, brut=brut,
                        resume=nom,
                        erreur=f"Outil « {nom} » inconnu. Disponibles : "
                               f"{', '.join(self.noms) or 'aucun'}.",
                    )
                )
                continue
            if nom not in actifs:
                resultat.append(
                    Proposition(
                        outil=nom, parametres=parametres, brut=brut, resume=nom,
                        erreur=f"Outil « {nom} » non activé pour cette conversation.",
                    )
                )
                continue
            resultat.append(outil.preparer(parametres, brut))
        return resultat

    async def executer(self, proposition: Proposition) -> Resultat:
        if proposition.erreur:
            return Resultat(
                outil=proposition.outil, resume=proposition.resume,
                succes=False, sortie=proposition.erreur,
            )
        outil = self._outils.get(proposition.outil)
        if outil is None:
            return Resultat(
                outil=proposition.outil, resume=proposition.resume, succes=False,
                sortie=f"Outil « {proposition.outil} » indisponible.",
            )
        logger.info(
            "Exécution de %s (%s) — %s",
            proposition.outil, proposition.niveau.value, proposition.resume,
        )
        return await outil.executer(proposition)

    def automatique(self, proposition: Proposition) -> bool:
        """Une proposition ne part seule que si elle est valide et en lecture."""
        return proposition.erreur is None and proposition.niveau is Niveau.LECTURE

    async def aclose(self) -> None:
        for outil in self._outils.values():
            await outil.aclose()
