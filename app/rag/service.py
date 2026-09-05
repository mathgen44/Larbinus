"""Orchestration du RAG : dépôt, indexation, recherche.

Ce module est le seul point d'entrée du reste de l'application vers le RAG.
Il reste utilisable même sans service d'embeddings configuré : dans ce cas
`disponible` vaut `False`, les documents peuvent être déposés mais restent en
attente d'indexation, et l'API le dit clairement plutôt que d'échouer de façon
obscure.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.rag.decoupage import Morceau, contextualiser, decouper
from app.rag.depot import DepotDocuments, IndexIncoherent, empreinte
from app.rag.embeddings import ClientEmbeddings, EmbeddingIndisponible
from app.rag.extraction import (
    EXTENSIONS,
    ExtractionImpossible,
    FormatNonSupporte,
    extraire,
)

logger = logging.getLogger("larbinus.rag")

#: Les embeddings sont calculés par paquets : un envoi par fragment
#: multiplierait les allers-retours réseau sans rien gagner.
TAILLE_LOT = 32


class ServiceRag:
    def __init__(self, depot: DepotDocuments, client: ClientEmbeddings | None, settings):
        self.depot = depot
        self.client = client
        self.settings = settings

    @property
    def disponible(self) -> bool:
        return self.client is not None

    @property
    def modele(self) -> str:
        return self.settings.embedding_model

    @property
    def dossier_depots(self) -> Path:
        """Où sont conservés les fichiers déposés depuis l'interface.

        Sans cette copie, un document dont l'indexation échoue — service
        d'embeddings arrêté, modèle absent — serait définitivement perdu :
        impossible de le réessayer sans que l'utilisateur le redépose.
        """
        return Path(self.settings.data_dir) / "depots"

    async def aclose(self) -> None:
        if self.client:
            await self.client.aclose()

    # -- dépôt et indexation ------------------------------------------------ #
    async def deposer(
        self, nom: str, donnees: bytes, source: str = "depot", chemin: str | None = None
    ) -> dict:
        """Enregistre un document, puis l'indexe si c'est possible.

        Un document déjà présent (même empreinte) n'est pas dupliqué : il est
        renvoyé tel quel, avec `doublon` à vrai.
        """
        sha = empreinte(donnees)
        existant = await self.depot.document_par_empreinte(sha)
        if existant:
            return {**existant, "doublon": True}

        if source == "depot":
            chemin = await self._conserver(nom, sha, donnees)

        document = await self.depot.enregistrer_document(
            nom=nom, sha=sha, taille=len(donnees), source=source, chemin=chemin
        )
        await self.indexer(document["id"], nom, donnees)
        return {**(await self.depot.document(document["id"])), "doublon": False}

    async def _conserver(self, nom: str, sha: str, donnees: bytes) -> str:
        """Range le fichier déposé sous son empreinte, pour pouvoir le relire."""
        extension = Path(nom).suffix.lower()
        cible = self.dossier_depots / f"{sha}{extension}"

        def ecrire() -> None:
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_bytes(donnees)

        await asyncio.to_thread(ecrire)
        return cible.name

    async def indexer(self, document_id: str, nom: str, donnees: bytes) -> None:
        """Extrait, découpe et vectorise un document. Ne lève jamais.

        Une erreur est enregistrée sur le document plutôt que propagée : un
        fichier illisible au milieu d'un dossier ne doit pas interrompre le
        traitement des autres.
        """
        if not self.disponible:
            await self.depot.marquer(
                document_id, "en_attente",
                erreur="Aucun service d'embeddings configuré (EMBEDDING_PROVIDER).",
            )
            return

        try:
            fragments = extraire(nom, donnees)
        except (FormatNonSupporte, ExtractionImpossible) as exc:
            logger.warning("Extraction impossible pour %s : %s", nom, exc)
            await self.depot.marquer(document_id, "erreur", erreur=str(exc))
            return
        except Exception as exc:  # pragma: no cover - garde-fou
            logger.exception("Extraction inattendue en échec pour %s", nom)
            await self.depot.marquer(document_id, "erreur", erreur=f"{type(exc).__name__}: {exc}")
            return

        morceaux = decouper(
            fragments,
            taille=self.settings.rag_chunk_size,
            chevauchement=self.settings.rag_chunk_overlap,
        )
        if not morceaux:
            await self.depot.marquer(document_id, "erreur", erreur="Document sans texte.")
            return

        try:
            vecteurs = await self._vectoriser(
                [contextualiser(m, nom) for m in morceaux]
            )
            await self.depot.remplacer_fragments(document_id, morceaux, vecteurs, self.modele)
        except (EmbeddingIndisponible, IndexIncoherent) as exc:
            logger.warning("Indexation de %s impossible : %s", nom, exc)
            await self.depot.marquer(document_id, "erreur", erreur=str(exc))
            return

        await self.depot.marquer(
            document_id, "indexe", erreur=None,
            fragments=len(morceaux), modele=self.modele,
        )
        logger.info("Document « %s » indexé en %d fragments", nom, len(morceaux))

    async def _vectoriser(self, textes: list[str]) -> list[list[float]]:
        vecteurs: list[list[float]] = []
        for debut in range(0, len(textes), TAILLE_LOT):
            lot = textes[debut : debut + TAILLE_LOT]
            vecteurs.extend(await self.client.vectoriser(lot))
        return vecteurs

    def _fichier_source(self, document: dict) -> Path | None:
        if not document.get("path"):
            return None
        racine = (
            Path(self.settings.documents_dir)
            if document["source"] == "dossier"
            else self.dossier_depots
        )
        return racine / document["path"]

    async def reindexer(self, document_id: str) -> dict | None:
        """Réessaie l'indexation à partir du fichier conservé."""
        document = await self.depot.document(document_id)
        if document is None:
            return None

        chemin = self._fichier_source(document)
        if chemin is None or not chemin.is_file():
            await self.depot.marquer(
                document_id, "erreur",
                erreur="Fichier d'origine introuvable : redéposez-le.",
            )
            return await self.depot.document(document_id)

        donnees = await asyncio.to_thread(chemin.read_bytes)
        await self.indexer(document_id, document["filename"], donnees)
        return await self.depot.document(document_id)

    async def reindexer_les_echecs(self) -> dict:
        """Réessaie tous les documents en erreur ou en attente.

        Le cas d'usage principal : le modèle d'embedding manquait, on vient de
        le récupérer, et l'on veut rattraper tout ce qui a échoué sans cliquer
        document par document.
        """
        documents = await self.depot.documents()
        a_reprendre = [d for d in documents if d["status"] != "indexe"]
        reussis = 0
        for document in a_reprendre:
            resultat = await self.reindexer(document["id"])
            if resultat and resultat["status"] == "indexe":
                reussis += 1
        return {"tentes": len(a_reprendre), "indexes": reussis}

    async def supprimer(self, document_id: str) -> bool:
        """Supprime le document, ses fragments et le fichier conservé."""
        document = await self.depot.document(document_id)
        if document is None:
            return False
        if document["source"] == "depot":
            chemin = self._fichier_source(document)
            if chemin and chemin.is_file():
                await asyncio.to_thread(chemin.unlink)
        return await self.depot.supprimer_document(document_id)

    # -- dossier surveillé -------------------------------------------------- #
    async def scanner(self) -> dict:
        """Parcourt le dossier surveillé et indexe ce qui est nouveau ou modifié."""
        racine = Path(self.settings.documents_dir)
        if not racine.is_dir():
            return {
                "dossier": str(racine),
                "existe": False,
                "message": "Dossier absent : montez-le dans le conteneur "
                           "(volume Docker) ou changez DOCUMENTS_DIR.",
                "ajoutes": 0, "inchanges": 0, "ignores": 0, "erreurs": 0,
            }

        fichiers = await asyncio.to_thread(
            lambda: sorted(
                chemin for chemin in racine.rglob("*") if chemin.is_file()
            )
        )

        ajoutes = inchanges = ignores = erreurs = 0
        for chemin in fichiers:
            if chemin.suffix.lower() not in EXTENSIONS:
                ignores += 1
                continue
            try:
                donnees = await asyncio.to_thread(chemin.read_bytes)
            except OSError as exc:
                logger.warning("Lecture impossible de %s : %s", chemin, exc)
                erreurs += 1
                continue

            if len(donnees) > self.settings.max_document_bytes:
                logger.warning("Fichier ignoré car trop volumineux : %s", chemin)
                ignores += 1
                continue

            relatif = str(chemin.relative_to(racine))
            resultat = await self.deposer(
                chemin.name, donnees, source="dossier", chemin=relatif
            )
            if resultat.get("doublon"):
                inchanges += 1
            elif resultat.get("status") == "erreur":
                erreurs += 1
            else:
                ajoutes += 1

        return {
            "dossier": str(racine),
            "existe": True,
            "ajoutes": ajoutes,
            "inchanges": inchanges,
            "ignores": ignores,
            "erreurs": erreurs,
        }

    # -- recherche ---------------------------------------------------------- #
    async def rechercher(self, question: str, limite: int | None = None) -> list[dict]:
        if not self.disponible or not question.strip():
            return []
        limite = limite or self.settings.rag_top_k
        vecteurs = await self.client.vectoriser([question])
        return await self.depot.rechercher(vecteurs[0], limite=limite)

    async def contexte(self, question: str, limite: int | None = None) -> tuple[str, list[dict]]:
        """Construit le bloc de contexte à injecter et la liste des sources.

        Les extraits sont numérotés pour que le modèle puisse s'y référer, et
        la consigne lui demande explicitement de dire quand la réponse ne s'y
        trouve pas — c'est ce qui distingue une réponse sourcée d'une
        invention plausible.
        """
        resultats = await self.rechercher(question, limite)
        if not resultats:
            return "", []

        blocs = []
        sources = []
        for numero, resultat in enumerate(resultats, start=1):
            origine = resultat["filename"]
            if resultat.get("heading"):
                origine += f" — {resultat['heading']}"
            if resultat.get("page"):
                origine += f" — page {resultat['page']}"
            blocs.append(f"[{numero}] {origine}\n{resultat['content']}")
            sources.append(
                {
                    "numero": numero,
                    "document_id": resultat["document_id"],
                    "filename": resultat["filename"],
                    "heading": resultat.get("heading"),
                    "page": resultat.get("page"),
                    "score": resultat.get("score"),
                    "extrait": resultat["content"][:400],
                }
            )

        contexte = (
            "Extraits des documents de l'utilisateur, pouvant servir à répondre.\n"
            "Appuie-toi dessus en priorité et cite le numéro de l'extrait utilisé, "
            "par exemple [1]. Si la réponse ne s'y trouve pas, dis-le clairement "
            "plutôt que de l'inventer.\n\n" + "\n\n".join(blocs)
        )
        return contexte, sources
