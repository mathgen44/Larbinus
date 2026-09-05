"""Authentification par clé d'API.

Deux en-têtes sont acceptés : `X-API-Key` (API native) et
`Authorization: Bearer <clé>` (ce qu'envoient les clients OpenAI).
Si `LARBINUS_API_KEY` est vide, l'accès est libre — cas d'un déploiement
sur un LAN de confiance.

Deux niveaux de protection :

* `require_api_key` garde les routes `/v1`, appelées par des clients qui
  savent porter une clé (n8n, scripts, SDK OpenAI) ;
* `require_api_key_ui` garde les routes `/api`, utilisées par l'interface web.
  Elle n'exige la clé que si `LARBINUS_PROTECT_UI` est vrai, car un navigateur
  ne peut pas en présenter une sans écran de saisie. Tant que ce réglage est
  faux, l'interface — et donc les documents indexés — reste accessible à toute
  machine du réseau.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.config import get_settings


def _extract_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _verifier(request: Request, expected: str | None) -> None:
    if not expected:
        return

    provided = _extract_key(request)
    # Comparaison à temps constant : évite de laisser fuiter la clé
    # caractère par caractère par mesure du temps de réponse.
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé d'API manquante ou invalide "
            "(en-tête `X-API-Key` ou `Authorization: Bearer`).",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_api_key(request: Request) -> None:
    """Protège les routes /v1, quel que soit le réglage de l'interface."""
    _verifier(request, get_settings().larbinus_api_key)


async def require_api_key_ui(request: Request) -> None:
    """Protège les routes /api, seulement si LARBINUS_PROTECT_UI est vrai."""
    settings = get_settings()
    if not settings.larbinus_protect_ui:
        return
    _verifier(request, settings.larbinus_api_key)
