"""Recherche web, via une instance SearXNG.

SearXNG plutôt qu'une API commerciale : aucune clé à gérer, aucune requête
envoyée à un tiers depuis Larbinus, et vous en avez déjà une. L'instance doit
seulement exposer le format JSON, désactivé par défaut — voir le README.

Toujours en lecture : une recherche ne modifie rien.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.outils.base import Niveau, Outil, Proposition, Resultat, tronquer

logger = logging.getLogger("larbinus.outils.web")


class OutilWeb(Outil):
    nom = "web"
    description = "Chercher sur le web. La recherche s'exécute sans confirmation."
    exemple = (
        "```larbinus:web\n"
        "requete: version stable de proxmox ve\n"
        "```"
    )

    def __init__(self, settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=settings.web_search_timeout,
                                  write=10.0, pool=5.0),
            follow_redirects=True,
        )

    @property
    def disponible(self) -> bool:
        return bool(self.settings.web_search_url)

    def preparer(self, parametres: dict[str, str], brut: str) -> Proposition:
        proposition = Proposition(
            outil=self.nom, parametres=parametres, brut=brut,
            niveau=Niveau.LECTURE, motif="recherche sans effet de bord",
        )
        requete = (
            parametres.get("requete") or parametres.get("query")
            or parametres.get("recherche") or ""
        ).strip()
        proposition.resume = requete or "?"
        if not requete:
            proposition.erreur = "Bloc incomplet : la clé « requete » est requise."
            return proposition
        proposition.parametres = {"requete": requete}
        return proposition

    async def executer(self, proposition: Proposition) -> Resultat:
        requete = proposition.parametres["requete"]
        debut = time.perf_counter()

        try:
            reponse = await self._client.get(
                self.settings.web_search_url,
                params={"q": requete, "format": "json"},
            )
        except httpx.HTTPError as exc:
            return Resultat(
                outil=self.nom, resume=requete, succes=False,
                sortie=f"Moteur de recherche injoignable : {exc}",
                duree_ms=round((time.perf_counter() - debut) * 1000),
            )

        if reponse.status_code == 403:
            # Symptôme très reconnaissable : SearXNG refuse `format=json` tant
            # qu'il n'est pas déclaré dans sa configuration.
            return Resultat(
                outil=self.nom, resume=requete, succes=False, code=403,
                sortie="Le moteur refuse le format JSON. Sur SearXNG, ajoutez "
                       "« json » à `search.formats` dans settings.yml, puis "
                       "redémarrez le service.",
            )
        if reponse.status_code != 200:
            return Resultat(
                outil=self.nom, resume=requete, succes=False,
                code=reponse.status_code,
                sortie=f"Le moteur a répondu {reponse.status_code}.",
            )

        try:
            charge = reponse.json()
        except ValueError:
            return Resultat(
                outil=self.nom, resume=requete, succes=False,
                sortie="Réponse du moteur illisible : le format JSON est-il activé ?",
            )

        resultats = charge.get("results", [])[: self.settings.web_search_results]
        if not resultats:
            return Resultat(
                outil=self.nom, resume=requete,
                sortie="Aucun résultat.",
                duree_ms=round((time.perf_counter() - debut) * 1000),
            )

        lignes = []
        for numero, entree in enumerate(resultats, start=1):
            titre = entree.get("title", "sans titre")
            url = entree.get("url", "")
            extrait = (entree.get("content") or "").strip()
            lignes.append(f"[{numero}] {titre}\n{url}\n{extrait}")

        texte, coupe = tronquer("\n\n".join(lignes), self.settings.tool_output_limit)
        return Resultat(
            outil=self.nom,
            resume=requete,
            sortie=texte,
            duree_ms=round((time.perf_counter() - debut) * 1000),
            tronque=coupe,
            meta={"resultats": len(resultats)},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
