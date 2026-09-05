"""Utilitaires de streaming partagés par l'API native et la couche OpenAI."""

from __future__ import annotations

import json
from typing import Any

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Désactive la mise en tampon d'un éventuel reverse proxy Nginx :
    # sans cela le flux arrive d'un bloc à la fin, et le streaming ne sert à rien.
    "X-Accel-Buffering": "no",
}


def sse_event(data: Any, event: str | None = None) -> str:
    """Sérialise un événement Server-Sent Events."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {payload}\n\n"
