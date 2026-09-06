"""Appels HTTP vers des services déclarés.

Souvent préférable à SSH pour interroger le homelab : une API refuse ce qu'elle
n'expose pas, là où un shell accepte tout. Interroger Portainer ou Proxmox par
leur API est plus sûr que de lancer `docker` ou `qm` à distance.

Deux garde-fous : une liste blanche d'hôtes, et la méthode HTTP. `GET` et
`HEAD` consultent, tout le reste modifie et attend une confirmation.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

import httpx

from app.outils.base import Niveau, Outil, Proposition, Resultat, tronquer

logger = logging.getLogger("larbinus.outils.http")

METHODES_DE_LECTURE = {"GET", "HEAD", "OPTIONS"}
METHODES_CONNUES = METHODES_DE_LECTURE | {"POST", "PUT", "PATCH", "DELETE"}


def analyser_hotes(declaration: str) -> set[str]:
    """`portainer.lan:9443, 192.168.0.40:8080` → ensemble d'hôtes autorisés."""
    return {h.strip().lower() for h in declaration.split(",") if h.strip()}


class OutilHTTP(Outil):
    nom = "http"
    description = (
        "Appeler une API d'un service déclaré. Les requêtes GET partent seules ; "
        "celles qui modifient quelque chose demandent une confirmation."
    )
    exemple = (
        "```larbinus:http\n"
        "url: http://<service autorisé>/api/endpoint\n"
        "methode: GET\n"
        "```"
    )

    def __init__(self, settings):
        self.settings = settings
        self.hotes = analyser_hotes(settings.http_allowed_hosts)
        self.entetes = self._charger_entetes(settings.http_headers)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=settings.http_timeout,
                                  write=10.0, pool=5.0),
            follow_redirects=False,   # une redirection sortirait de la liste blanche
        )

    @staticmethod
    def _charger_entetes(declaration: str | None) -> dict[str, dict[str, str]]:
        if not declaration:
            return {}
        try:
            charge = json.loads(declaration)
        except json.JSONDecodeError as exc:
            logger.warning("HTTP_HEADERS illisible (%s) : en-têtes ignorés.", exc)
            return {}
        return {hote.lower(): entetes for hote, entetes in charge.items()}

    @property
    def disponible(self) -> bool:
        return bool(self.hotes)

    def _cible(self, url: str) -> str | None:
        """Hôte de l'URL s'il est autorisé, sinon None."""
        decoupe = urlparse(url)
        if decoupe.scheme not in ("http", "https") or not decoupe.hostname:
            return None
        # Le port compte : un service autorisé sur 9000 ne l'est pas sur 22.
        avec_port = decoupe.netloc.lower()
        sans_port = decoupe.hostname.lower()
        if avec_port in self.hotes or sans_port in self.hotes:
            return avec_port
        return None

    def preparer(self, parametres: dict[str, str], brut: str) -> Proposition:
        proposition = Proposition(outil=self.nom, parametres=parametres, brut=brut)

        url = (parametres.get("url") or "").strip()
        methode = (parametres.get("methode") or parametres.get("method") or "GET").upper()
        corps = parametres.get("corps") or parametres.get("body") or ""
        proposition.resume = f"{methode} {url or '?'}"

        if not url:
            proposition.erreur = "Bloc incomplet : la clé « url » est requise."
            return proposition
        if methode not in METHODES_CONNUES:
            proposition.erreur = (
                f"Méthode « {methode} » non prise en charge. "
                f"Attendu : {', '.join(sorted(METHODES_CONNUES))}."
            )
            return proposition
        if self._cible(url) is None:
            autorises = ", ".join(sorted(self.hotes)) or "aucun"
            proposition.erreur = (
                f"Hôte non autorisé. Services déclarés : {autorises}."
            )
            return proposition

        proposition.parametres = {"url": url, "methode": methode}
        if corps:
            proposition.parametres["corps"] = corps

        if methode in METHODES_DE_LECTURE:
            proposition.niveau, proposition.motif = Niveau.LECTURE, "requête de consultation"
        else:
            proposition.niveau, proposition.motif = (
                Niveau.ECRITURE,
                f"{methode} modifie l'état du service",
            )
        return proposition

    async def executer(self, proposition: Proposition) -> Resultat:
        url = proposition.parametres["url"]
        methode = proposition.parametres["methode"]
        corps = proposition.parametres.get("corps")
        hote = self._cible(url) or urlparse(url).hostname or ""

        entetes = dict(self.entetes.get(hote, {}))
        entetes.setdefault(
            "Accept", "application/json, text/plain;q=0.9, */*;q=0.5"
        )
        if corps and "Content-Type" not in entetes:
            entetes["Content-Type"] = "application/json"

        debut = time.perf_counter()
        try:
            reponse = await self._client.request(
                methode, url, headers=entetes, content=corps or None
            )
        except httpx.HTTPError as exc:
            return Resultat(
                outil=self.nom, resume=proposition.resume, succes=False,
                sortie=f"Service injoignable : {type(exc).__name__} — {exc}",
                duree_ms=round((time.perf_counter() - debut) * 1000),
            )

        texte = reponse.text
        # Un JSON réindenté se lit bien mieux dans le contexte du modèle qu'une
        # ligne unique de plusieurs milliers de caractères.
        if "json" in reponse.headers.get("content-type", ""):
            try:
                texte = json.dumps(reponse.json(), ensure_ascii=False, indent=2)
            except ValueError:
                pass

        texte, coupe = tronquer(texte.strip(), self.settings.tool_output_limit)
        return Resultat(
            outil=self.nom,
            resume=proposition.resume,
            sortie=texte or "(réponse vide)",
            succes=reponse.status_code < 400,
            code=reponse.status_code,
            duree_ms=round((time.perf_counter() - debut) * 1000),
            tronque=coupe,
            meta={"hote": hote},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
