"""Dépôt documentaire : stockage des documents, des fragments et des vecteurs.

Les vecteurs sont rangés dans SQLite via l'extension **sqlite-vec**, qui ajoute
une table virtuelle capable de recherche par plus proches voisins. Si
l'extension ne peut pas être chargée — build de Python sans support des
extensions, par exemple — on bascule sur un calcul de similarité en Python.
C'est plus lent, mais parfaitement viable à l'échelle d'un homelab, et cela
évite qu'une contrainte d'environnement rende toute la fonctionnalité
inutilisable.

La dimension des vecteurs dépend du modèle d'embedding. Elle est enregistrée à
la première indexation : changer de modèle ensuite rend l'index incohérent, ce
qui est détecté et signalé plutôt que de produire silencieusement des
résultats faux.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import struct
import uuid
from typing import Any

from app.storage.db import Database, maintenant

logger = logging.getLogger("larbinus.rag.depot")


def empreinte(donnees: bytes) -> str:
    return hashlib.sha256(donnees).hexdigest()


def _serialiser(vecteur: list[float]) -> bytes:
    return struct.pack(f"{len(vecteur)}f", *vecteur)


def _deserialiser(donnees: bytes) -> list[float]:
    return list(struct.unpack(f"{len(donnees) // 4}f", donnees))


def cosinus(a: list[float], b: list[float]) -> float:
    produit = sum(x * y for x, y in zip(a, b))
    norme_a = math.sqrt(sum(x * x for x in a))
    norme_b = math.sqrt(sum(y * y for y in b))
    if not norme_a or not norme_b:
        return 0.0
    return produit / (norme_a * norme_b)


class IndexIncoherent(Exception):
    """Le modèle d'embedding a changé depuis la dernière indexation."""


class DepotDocuments:
    def __init__(self, db: Database):
        self.db = db
        self.vec_disponible = False
        self.dimension: int | None = None

    # -- initialisation ---------------------------------------------------- #
    async def preparer(self) -> None:
        """Charge sqlite-vec si possible et relit l'état de l'index."""
        self.vec_disponible = await self.db._ecrire(self._charger_extension)
        etat = await self.db._lire("SELECT * FROM rag_meta WHERE id = 1")
        if etat:
            self.dimension = etat[0]["dimension"]
            if self.dimension and self.vec_disponible:
                await self.db._ecrire(
                    lambda conn: self._creer_table_vecteurs(conn, self.dimension)
                )
        logger.info(
            "Index vectoriel : %s%s",
            "sqlite-vec" if self.vec_disponible else "repli en Python (sans sqlite-vec)",
            f", dimension {self.dimension}" if self.dimension else "",
        )

    @staticmethod
    def _charger_extension(conn: sqlite3.Connection) -> bool:
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("SELECT vec_version()").fetchone()
            return True
        except Exception as exc:
            logger.warning(
                "sqlite-vec indisponible (%s) : recherche vectorielle en Python.", exc
            )
            return False

    @staticmethod
    def _creer_table_vecteurs(conn: sqlite3.Connection, dimension: int) -> None:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
            f"USING vec0(embedding float[{dimension}])"
        )

    async def _fixer_dimension(self, dimension: int, modele: str) -> None:
        """Enregistre la dimension et le modèle utilisés par l'index."""
        if self.dimension is None:
            def action(conn):
                conn.execute(
                    "INSERT OR REPLACE INTO rag_meta (id, dimension, model, updated_at)"
                    " VALUES (1, ?, ?, ?)",
                    (dimension, modele, maintenant()),
                )
                if self.vec_disponible:
                    self._creer_table_vecteurs(conn, dimension)

            await self.db._ecrire(action)
            self.dimension = dimension
            return

        if dimension != self.dimension:
            raise IndexIncoherent(
                f"L'index contient des vecteurs de dimension {self.dimension}, "
                f"le modèle « {modele} » en produit {dimension}. Changer de modèle "
                "d'embedding impose de réindexer tous les documents."
            )

    async def etat_index(self) -> dict:
        lignes = await self.db._lire("SELECT * FROM rag_meta WHERE id = 1")
        meta = dict(lignes[0]) if lignes else {}
        compte = await self.db._lire(
            "SELECT COUNT(*) AS documents,"
            " (SELECT COUNT(*) FROM chunks) AS fragments FROM documents"
        )
        return {
            "moteur": "sqlite-vec" if self.vec_disponible else "python",
            "dimension": meta.get("dimension"),
            "embedding_model": meta.get("model"),
            "documents": compte[0]["documents"],
            "fragments": compte[0]["fragments"],
        }

    # -- documents --------------------------------------------------------- #
    async def documents(self) -> list[dict]:
        lignes = await self.db._lire(
            "SELECT * FROM documents ORDER BY created_at DESC"
        )
        return [dict(ligne) for ligne in lignes]

    async def document(self, identifiant: str) -> dict | None:
        lignes = await self.db._lire(
            "SELECT * FROM documents WHERE id = ?", (identifiant,)
        )
        return dict(lignes[0]) if lignes else None

    async def document_par_empreinte(self, sha: str) -> dict | None:
        lignes = await self.db._lire("SELECT * FROM documents WHERE sha256 = ?", (sha,))
        return dict(lignes[0]) if lignes else None

    async def enregistrer_document(
        self,
        nom: str,
        sha: str,
        taille: int,
        source: str,
        chemin: str | None = None,
    ) -> dict:
        identifiant = uuid.uuid4().hex
        horodatage = maintenant()

        def action(conn):
            conn.execute(
                """
                INSERT INTO documents (id, source, path, filename, bytes, sha256,
                                       chunk_count, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'en_attente', ?, ?)
                """,
                (identifiant, source, chemin, nom, taille, sha, horodatage, horodatage),
            )

        await self.db._ecrire(action)
        return await self.document(identifiant)

    async def marquer(
        self,
        identifiant: str,
        statut: str,
        erreur: str | None = None,
        fragments: int | None = None,
        modele: str | None = None,
    ) -> None:
        def action(conn):
            conn.execute(
                """
                UPDATE documents
                   SET status = ?, error = ?, updated_at = ?,
                       chunk_count = COALESCE(?, chunk_count),
                       embedding_model = COALESCE(?, embedding_model)
                 WHERE id = ?
                """,
                (statut, erreur, maintenant(), fragments, modele, identifiant),
            )

        await self.db._ecrire(action)

    async def supprimer_document(self, identifiant: str) -> bool:
        def action(conn):
            if self.vec_disponible:
                # La table virtuelle ignore les clés étrangères : ses lignes
                # doivent être retirées explicitement, sinon l'index garde des
                # vecteurs orphelins qui ressortiront dans les recherches.
                rowids = [
                    ligne[0]
                    for ligne in conn.execute(
                        "SELECT id FROM chunks WHERE document_id = ?", (identifiant,)
                    )
                ]
                for rowid in rowids:
                    conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (rowid,))
            return conn.execute(
                "DELETE FROM documents WHERE id = ?", (identifiant,)
            ).rowcount

        return bool(await self.db._ecrire(action))

    # -- fragments et vecteurs --------------------------------------------- #
    async def remplacer_fragments(
        self,
        document_id: str,
        morceaux: list[Any],
        vecteurs: list[list[float]],
        modele: str,
    ) -> int:
        """Réécrit tous les fragments d'un document et leurs vecteurs."""
        if not morceaux:
            return 0
        await self._fixer_dimension(len(vecteurs[0]), modele)
        horodatage = maintenant()
        vec_disponible = self.vec_disponible

        def action(conn):
            anciens = [
                ligne[0]
                for ligne in conn.execute(
                    "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
                )
            ]
            if vec_disponible:
                for rowid in anciens:
                    conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (rowid,))
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))

            for morceau, vecteur in zip(morceaux, vecteurs):
                brut = _serialiser(vecteur)
                curseur = conn.execute(
                    """
                    INSERT INTO chunks (document_id, ordinal, content, heading,
                                        page, embedding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (document_id, morceau.ordinal, morceau.contenu, morceau.titre,
                     morceau.page, brut, horodatage),
                )
                if vec_disponible:
                    conn.execute(
                        "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                        (curseur.lastrowid, brut),
                    )
            return len(morceaux)

        return await self.db._ecrire(action)

    async def rechercher(
        self, vecteur: list[float], limite: int = 5
    ) -> list[dict]:
        """Fragments les plus proches, avec leur document d'origine."""
        if self.dimension is None:
            return []
        if len(vecteur) != self.dimension:
            raise IndexIncoherent(
                f"La question a été vectorisée en dimension {len(vecteur)} alors que "
                f"l'index en attend {self.dimension}. Réindexez les documents."
            )

        if self.vec_disponible:
            lignes = await self.db._lire(
                """
                SELECT c.id, c.content, c.heading, c.page, c.ordinal,
                       d.id AS document_id, d.filename, v.distance
                  FROM vec_chunks v
                  JOIN chunks c ON c.id = v.rowid
                  JOIN documents d ON d.id = c.document_id
                 WHERE v.embedding MATCH ? AND k = ?
                 ORDER BY v.distance
                """,
                (_serialiser(vecteur), limite),
            )
            resultats = []
            for ligne in lignes:
                entree = dict(ligne)
                distance = entree.pop("distance")
                # Distance L2 sur vecteurs quelconques : on la convertit en un
                # score décroissant, seul l'ordre relatif ayant du sens ici.
                entree["score"] = round(1.0 / (1.0 + distance), 4)
                resultats.append(entree)
            return resultats

        # Repli : similarité cosinus calculée en Python.
        lignes = await self.db._lire(
            """
            SELECT c.id, c.content, c.heading, c.page, c.ordinal, c.embedding,
                   d.id AS document_id, d.filename
              FROM chunks c JOIN documents d ON d.id = c.document_id
             WHERE c.embedding IS NOT NULL
            """
        )
        classes = []
        for ligne in lignes:
            entree = dict(ligne)
            brut = entree.pop("embedding")
            entree["score"] = round(cosinus(vecteur, _deserialiser(brut)), 4)
            classes.append(entree)
        classes.sort(key=lambda e: e["score"], reverse=True)
        return classes[:limite]

    async def reinitialiser_index(self) -> None:
        """Vide fragments et vecteurs — utilisé au changement de modèle."""
        def action(conn):
            if self.vec_disponible:
                conn.execute("DELETE FROM vec_chunks")
            conn.execute("DELETE FROM chunks")
            conn.execute("UPDATE documents SET status = 'en_attente', chunk_count = 0")
            conn.execute("DELETE FROM rag_meta WHERE id = 1")

        await self.db._ecrire(action)
        self.dimension = None
