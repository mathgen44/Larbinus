"""Persistance des conversations.

SQLite via le module standard `sqlite3`, plutôt qu'un pilote asynchrone
supplémentaire : le volume d'écritures est minuscule (quelques lignes par
réponse) et chaque appel est dépaysé dans un fil d'exécution par
`asyncio.to_thread`, ce qui suffit largement et évite une dépendance de plus.

Le mode WAL permet aux lectures de ne pas bloquer les écritures — utile quand
l'interface rafraîchit la liste des conversations pendant qu'une réponse
s'écrit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("larbinus.db")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    model       TEXT,
    system      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    reasoning       TEXT,
    model           TEXT,
    provider        TEXT,
    usage_json      TEXT,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);
"""

TITRE_PAR_DEFAUT = "Nouvelle conversation"


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def titre_depuis(texte: str, longueur: int = 60) -> str:
    """Première ligne du message, tronquée — sert de titre automatique."""
    ligne = " ".join(texte.strip().split())
    if not ligne:
        return TITRE_PAR_DEFAUT
    return ligne if len(ligne) <= longueur else ligne[: longueur - 1].rstrip() + "…"


class Database:
    def __init__(self, chemin: str | Path):
        self.chemin = Path(chemin)
        self._conn: sqlite3.Connection | None = None
        # Sérialise les écritures : une seule connexion partagée, et SQLite
        # n'aime pas les écritures concurrentes sur la même connexion.
        self._verrou = asyncio.Lock()

    # -- cycle de vie ------------------------------------------------------ #
    def _ouvrir(self) -> sqlite3.Connection:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.chemin, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            logger.info("Schéma de la base initialisé en version %s", SCHEMA_VERSION)
        conn.commit()
        return conn

    async def connect(self) -> None:
        self._conn = await asyncio.to_thread(self._ouvrir)
        logger.info("Base de données ouverte : %s", self.chemin)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Base non ouverte : appelez connect() d'abord.")
        return self._conn

    # -- lectures ---------------------------------------------------------- #
    async def _lire(self, requete: str, parametres: tuple = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(
            lambda: self.conn.execute(requete, parametres).fetchall()
        )

    async def liste_conversations(self, limite: int = 200) -> list[dict]:
        lignes = await self._lire(
            """
            SELECT c.id, c.title, c.model, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                       AS message_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limite,),
        )
        return [dict(ligne) for ligne in lignes]

    async def conversation(self, identifiant: str) -> dict | None:
        lignes = await self._lire(
            "SELECT * FROM conversations WHERE id = ?", (identifiant,)
        )
        return dict(lignes[0]) if lignes else None

    async def messages(self, identifiant: str) -> list[dict]:
        lignes = await self._lire(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
            (identifiant,),
        )
        messages = []
        for ligne in lignes:
            message = dict(ligne)
            message["usage"] = json.loads(message.pop("usage_json") or "{}")
            messages.append(message)
        return messages

    async def historique(self, identifiant: str) -> list[dict]:
        """Historique au format attendu par les fournisseurs : rôle + contenu.

        Le raisonnement est délibérément exclu : le renvoyer au modèle au tour
        suivant gonfle le contexte sans rien apporter, et certains fournisseurs
        le refusent carrément.
        """
        return [
            {"role": m["role"], "content": m["content"]}
            for m in await self.messages(identifiant)
            if m["content"]
        ]

    # -- écritures --------------------------------------------------------- #
    async def _ecrire(self, action):
        async with self._verrou:
            return await asyncio.to_thread(self._transaction, action)

    def _transaction(self, action):
        try:
            resultat = action(self.conn)
            self.conn.commit()
            return resultat
        except Exception:
            self.conn.rollback()
            raise

    async def creer_conversation(
        self,
        titre: str | None = None,
        modele: str | None = None,
        systeme: str | None = None,
    ) -> dict:
        identifiant = uuid.uuid4().hex
        horodatage = maintenant()

        def action(conn):
            conn.execute(
                "INSERT INTO conversations (id, title, model, system, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (identifiant, titre or TITRE_PAR_DEFAUT, modele, systeme,
                 horodatage, horodatage),
            )

        await self._ecrire(action)
        return {
            "id": identifiant,
            "title": titre or TITRE_PAR_DEFAUT,
            "model": modele,
            "system": systeme,
            "created_at": horodatage,
            "updated_at": horodatage,
            "message_count": 0,
        }

    async def modifier_conversation(self, identifiant: str, **champs) -> dict | None:
        autorises = {"title", "model", "system"}
        mises_a_jour = {k: v for k, v in champs.items() if k in autorises and v is not None}
        if not mises_a_jour:
            return await self.conversation(identifiant)

        mises_a_jour["updated_at"] = maintenant()
        affectation = ", ".join(f"{k} = ?" for k in mises_a_jour)

        def action(conn):
            conn.execute(
                f"UPDATE conversations SET {affectation} WHERE id = ?",
                (*mises_a_jour.values(), identifiant),
            )

        await self._ecrire(action)
        return await self.conversation(identifiant)

    async def supprimer_conversation(self, identifiant: str) -> bool:
        def action(conn):
            return conn.execute(
                "DELETE FROM conversations WHERE id = ?", (identifiant,)
            ).rowcount

        return bool(await self._ecrire(action))

    async def ajouter_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        reasoning: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        usage: dict | None = None,
        duration_ms: int | None = None,
    ) -> int:
        horodatage = maintenant()

        def action(conn):
            curseur = conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, reasoning,
                                      model, provider, usage_json, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, reasoning, model, provider,
                 json.dumps(usage) if usage else None, duration_ms, horodatage),
            )
            # Le titre par défaut est remplacé par le début de la première
            # question : une liste de « Nouvelle conversation » est inutilisable.
            if role == "user":
                conn.execute(
                    "UPDATE conversations SET title = ? WHERE id = ? AND title = ?",
                    (titre_depuis(content), conversation_id, TITRE_PAR_DEFAUT),
                )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (horodatage, conversation_id),
            )
            return curseur.lastrowid

        return await self._ecrire(action)
