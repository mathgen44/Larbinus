"""Intergiciels : identifiant de requête, journal d'accès, limitation de débit."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("larbinus.acces")

#: Ces routes ne sont ni journalisées ni comptées : la sonde Docker les appelle
#: toutes les 30 secondes, et l'interface tire ses fichiers statiques en rafale.
EXEMPTES = ("/health", "/static", "/favicon.ico")


class JournalJson(logging.Formatter):
    """Formate chaque ligne de journal en JSON, pour être exploitable ailleurs.

    Un journal en texte libre se lit bien à l'œil et se traite mal ; le JSON
    fait l'inverse. Le choix se fait par `LOG_FORMAT`, sans que le code appelant
    ait à s'en soucier.
    """

    def format(self, enregistrement: logging.LogRecord) -> str:
        charge = {
            "horodatage": self.formatTime(enregistrement, "%Y-%m-%dT%H:%M:%S%z"),
            "niveau": enregistrement.levelname,
            "source": enregistrement.name,
            "message": enregistrement.getMessage(),
        }
        for champ in ("request_id", "methode", "chemin", "statut", "duree_ms", "client"):
            valeur = getattr(enregistrement, champ, None)
            if valeur is not None:
                charge[champ] = valeur
        if enregistrement.exc_info:
            charge["exception"] = self.formatException(enregistrement.exc_info)
        return json.dumps(charge, ensure_ascii=False)


def adresse_client(request: Request, proxys_de_confiance: set[str]) -> str:
    """Adresse réelle du client, en tenant compte d'un reverse proxy.

    `X-Forwarded-For` n'est cru **que** si la connexion vient d'un proxy
    déclaré : sans cette précaution, n'importe qui pourrait usurper une adresse
    et contourner la limitation de débit en changeant un en-tête.
    """
    directe = request.client.host if request.client else "inconnu"
    if directe in proxys_de_confiance:
        transmis = request.headers.get("x-forwarded-for", "")
        if transmis:
            return transmis.split(",")[0].strip()
    return directe


class ContexteRequete(BaseHTTPMiddleware):
    """Attribue un identifiant à chaque requête et journalise son issue."""

    async def dispatch(self, request: Request, call_next):
        identifiant = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = identifiant

        debut = time.perf_counter()
        try:
            reponse = await call_next(request)
        except Exception:
            duree = round((time.perf_counter() - debut) * 1000)
            logger.exception(
                "Requête en échec",
                extra={
                    "request_id": identifiant,
                    "methode": request.method,
                    "chemin": request.url.path,
                    "duree_ms": duree,
                },
            )
            raise

        duree = round((time.perf_counter() - debut) * 1000)
        reponse.headers["X-Request-ID"] = identifiant

        if not request.url.path.startswith(EXEMPTES):
            logger.info(
                "%s %s → %s",
                request.method,
                request.url.path,
                reponse.status_code,
                extra={
                    "request_id": identifiant,
                    "methode": request.method,
                    "chemin": request.url.path,
                    "statut": reponse.status_code,
                    "duree_ms": duree,
                },
            )
        return reponse


class LimitationDebit(BaseHTTPMiddleware):
    """Plafond glissant de requêtes par adresse.

    Volontairement en mémoire : un seul conteneur, pas de Redis à maintenir.
    Le compteur repart donc à zéro au redémarrage, ce qui est sans conséquence
    pour l'usage visé — empêcher qu'un script en boucle ne vide un quota d'API
    payante.
    """

    def __init__(self, app, limite: int, fenetre: int, proxys: set[str]):
        super().__init__(app)
        self.limite = limite
        self.fenetre = fenetre
        self.proxys = proxys
        self._historique: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if self.limite <= 0 or request.url.path.startswith(EXEMPTES):
            return await call_next(request)

        client = adresse_client(request, self.proxys)
        maintenant = time.monotonic()
        horodatages = self._historique.setdefault(client, deque())

        while horodatages and maintenant - horodatages[0] > self.fenetre:
            horodatages.popleft()

        if len(horodatages) >= self.limite:
            attente = int(self.fenetre - (maintenant - horodatages[0])) + 1
            logger.warning(
                "Débit dépassé pour %s (%s requêtes en %ss)",
                client, len(horodatages), self.fenetre,
                extra={"client": client, "chemin": request.url.path},
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "type": "DebitDepasse",
                        "message": f"Trop de requêtes : {self.limite} maximum par "
                                   f"{self.fenetre} secondes. Réessayez dans {attente} s.",
                    }
                },
                headers={"Retry-After": str(attente)},
            )

        horodatages.append(maintenant)

        # Purge occasionnelle : sans elle, chaque adresse vue une fois
        # resterait en mémoire indéfiniment.
        if len(self._historique) > 2048:
            self._historique = {
                adresse: file
                for adresse, file in self._historique.items()
                if file and maintenant - file[-1] <= self.fenetre
            }

        reponse: Response = await call_next(request)
        reponse.headers["X-RateLimit-Limit"] = str(self.limite)
        reponse.headers["X-RateLimit-Remaining"] = str(
            max(0, self.limite - len(horodatages))
        )
        return reponse
