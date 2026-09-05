"""Authentification par clé d'API.

Deux en-têtes sont acceptés : `X-API-Key` (API native) et
`Authorization: Bearer <clé>` (ce qu'envoient les clients OpenAI).
Si `LARBINUS_API_KEY` est vide, l'accès est libre — cas d'un déploiement
sur un LAN de confiance.
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


async def require_api_key(request: Request) -> None:
    """Dépendance FastAPI : protège les routes /api et /v1."""
    expected = get_settings().larbinus_api_key
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
