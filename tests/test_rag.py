"""Socle documentaire du RAG (phase 7a) — aucun appel réseau réel."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag.decoupage import contextualiser, decouper
from app.rag.depot import DepotDocuments, cosinus, empreinte
from app.rag.extraction import (
    ExtractionImpossible,
    Fragment,
    FormatNonSupporte,
    extraire,
)
from app.rag.service import ServiceRag
from app.storage.db import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class EmbeddingsFactices:
    """Vectorise sans réseau : un sac de mots projeté sur 16 dimensions.

    Grossier, mais suffisant pour que deux textes partageant du vocabulaire
    soient plus proches que deux textes étrangers — c'est tout ce que les
    tests ont besoin de vérifier.
    """

    dimension = 16

    def __init__(self):
        self.appels = 0

    async def vectoriser(self, textes: list[str]) -> list[list[float]]:
        self.appels += 1
        vecteurs = []
        for texte in textes:
            vecteur = [0.0] * self.dimension
            for mot in texte.lower().split():
                vecteur[hash(mot) % self.dimension] += 1.0
            vecteurs.append(vecteur)
        return vecteurs

    async def aclose(self):
        pass


@pytest.fixture
async def rag(tmp_path):
    import app.main as principal

    principal.settings.data_dir = str(tmp_path)
    principal.settings.documents_dir = str(tmp_path / "documents")

    db = Database(tmp_path / "rag.db")
    await db.connect()
    depot = DepotDocuments(db)
    await depot.preparer()
    service = ServiceRag(depot, EmbeddingsFactices(), principal.settings)
    yield service
    await db.close()


# --------------------------------------------------------------------------- #
#  Extraction
# --------------------------------------------------------------------------- #
def test_markdown_decoupe_par_titres():
    contenu = b"""# Installation

Il faut d'abord installer Docker sur la machine cible.

## Configuration reseau

Ouvrir le port 8474 dans le pare-feu.
"""
    fragments = extraire("guide.md", contenu)
    assert [f.titre for f in fragments] == ["Installation", "Configuration reseau"]
    assert "Docker" in fragments[0].texte


def test_html_ignore_scripts_et_navigation():
    html = b"""<html><head><title>Page</title></head><body>
    <nav>Menu inutile</nav>
    <script>var poison = 1;</script>
    <h1>Titre</h1><p>Contenu utile.</p>
    <footer>Pied de page</footer></body></html>"""
    fragments = extraire("page.html", html)
    texte = " ".join(f.texte for f in fragments)
    assert "Contenu utile" in texte
    assert "poison" not in texte
    assert "Menu inutile" not in texte


def test_format_inconnu_refuse():
    with pytest.raises(FormatNonSupporte):
        extraire("archive.zip", b"...")


def test_pdf_scanne_donne_un_message_explicite():
    """Un PDF sans couche texte est le piège le plus courant."""
    from pypdf import PdfWriter

    tampon = io.BytesIO()
    ecrivain = PdfWriter()
    ecrivain.add_blank_page(width=200, height=200)
    ecrivain.write(tampon)

    with pytest.raises(ExtractionImpossible) as exc:
        extraire("scan.pdf", tampon.getvalue())
    assert "scan" in str(exc.value).lower()


def test_docx_et_xlsx():
    import docx
    from openpyxl import Workbook

    document = docx.Document()
    document.add_heading("Procedure", level=1)
    document.add_paragraph("Redemarrer le service avant toute chose.")
    tampon = io.BytesIO()
    document.save(tampon)
    fragments = extraire("procedure.docx", tampon.getvalue())
    assert fragments[0].titre == "Procedure"
    assert "Redemarrer" in fragments[0].texte

    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Inventaire"
    feuille.append(["Machine", "Role"])
    feuille.append(["beast", "Proxmox"])
    tampon = io.BytesIO()
    classeur.save(tampon)
    fragments = extraire("inventaire.xlsx", tampon.getvalue())
    assert fragments[0].titre == "Inventaire"
    assert "beast" in fragments[0].texte


# --------------------------------------------------------------------------- #
#  Découpage
# --------------------------------------------------------------------------- #
def test_decoupage_respecte_les_phrases_et_chevauche():
    phrase = "Ceci est une phrase de test suffisamment longue pour compter. "
    fragments = [Fragment(texte=phrase * 40, titre="Section")]
    morceaux = decouper(fragments, taille=300, chevauchement=100)

    assert len(morceaux) > 1
    assert all(m.titre == "Section" for m in morceaux)
    # Aucun morceau ne doit dépasser franchement la cible.
    assert all(len(m.contenu) <= 420 for m in morceaux)
    # Le chevauchement doit faire réapparaître la fin du morceau précédent.
    assert morceaux[0].contenu.split(".")[-2].strip() in morceaux[1].contenu


def test_phrase_plus_longue_que_la_cible_est_coupee():
    morceaux = decouper([Fragment(texte="a" * 900)], taille=300, chevauchement=50)
    assert len(morceaux) == 3


def test_contexte_porte_la_provenance():
    morceaux = decouper([Fragment(texte="Le port est 8474." * 20, titre="Reseau", page=3)])
    texte = contextualiser(morceaux[0], "guide.pdf")
    assert texte.startswith("[guide.pdf — Reseau — page 3]")


# --------------------------------------------------------------------------- #
#  Dépôt et recherche
# --------------------------------------------------------------------------- #
async def test_indexation_et_recherche(rag):
    await rag.deposer(
        "reseau.md",
        "# Pare-feu\n\nLe port 8474 doit etre ouvert pour Larbinus.\n".encode(),
    )
    await rag.deposer(
        "cuisine.md",
        "# Tarte\n\nMelanger la farine et le beurre puis cuire vingt minutes.\n".encode(),
    )

    documents = await rag.depot.documents()
    assert {d["status"] for d in documents} == {"indexe"}
    assert all(d["chunk_count"] >= 1 for d in documents)

    resultats = await rag.rechercher("Quel port ouvrir dans le pare-feu ?", limite=1)
    assert resultats and resultats[0]["filename"] == "reseau.md"


async def test_meme_fichier_depose_deux_fois_n_est_pas_duplique(rag):
    donnees = b"# Note\n\nContenu quelconque mais assez long pour etre indexe.\n"
    premier = await rag.deposer("note.md", donnees)
    second = await rag.deposer("note-copie.md", donnees)

    assert second["doublon"] is True
    assert second["id"] == premier["id"]
    assert len(await rag.depot.documents()) == 1


async def test_document_illisible_est_marque_en_erreur_sans_lever(rag):
    resultat = await rag.deposer("photo.png", b"\x89PNG\r\n\x1a\n")
    assert resultat["status"] == "erreur"
    assert "non pris en charge" in resultat["error"].lower()


async def test_suppression_retire_fragments_et_vecteurs(rag):
    document = await rag.deposer(
        "a-supprimer.md", b"# Titre\n\nUn contenu assez long pour produire un fragment.\n"
    )
    assert await rag.rechercher("contenu", limite=5)

    await rag.depot.supprimer_document(document["id"])
    assert await rag.depot.documents() == []
    assert await rag.rechercher("contenu", limite=5) == []


async def test_contexte_numerote_et_cite_les_sources(rag):
    await rag.deposer(
        "procedure.md",
        "# Redemarrage\n\nPour redemarrer le conteneur, lancer docker compose restart.\n".encode(),
    )
    contexte, sources = await rag.contexte("Comment redemarrer le conteneur ?", limite=1)

    assert "[1]" in contexte
    assert "docker compose restart" in contexte
    # La consigne anti-invention doit accompagner les extraits.
    assert "dis-le clairement" in contexte
    assert sources[0]["filename"] == "procedure.md"
    assert sources[0]["numero"] == 1


async def test_sans_embeddings_le_document_reste_en_attente(rag):
    rag.client = None
    resultat = await rag.deposer("note.md", b"# Titre\n\nUn contenu quelconque ici.\n")
    assert resultat["status"] == "en_attente"
    assert "EMBEDDING_PROVIDER" in resultat["error"]
    assert await rag.rechercher("contenu") == []


async def test_changement_de_modele_detecte(rag):
    """Des vecteurs de dimensions différentes ne sont pas comparables."""
    from app.rag.depot import IndexIncoherent

    await rag.deposer("note.md", b"# Titre\n\nUn contenu suffisamment long ici.\n")
    with pytest.raises(IndexIncoherent):
        await rag.depot.rechercher([0.0] * 8, limite=3)


async def test_repli_sans_sqlite_vec(tmp_path):
    """Le filet de sécurité doit fonctionner : même résultats, sans l'extension."""
    import app.main as principal

    principal.settings.data_dir = str(tmp_path)
    db = Database(tmp_path / "repli.db")
    await db.connect()
    depot = DepotDocuments(db)
    await depot.preparer()
    # On force le repli en Python, comme si l'extension n'avait pas pu être chargée.
    depot.vec_disponible = False

    service = ServiceRag(depot, EmbeddingsFactices(), principal.settings)
    await service.deposer(
        "reseau.md", b"# Pare-feu\n\nLe port 8474 doit etre ouvert pour Larbinus.\n"
    )
    await service.deposer(
        "cuisine.md", b"# Tarte\n\nMelanger la farine et le beurre puis cuire.\n"
    )

    etat = await depot.etat_index()
    assert etat["moteur"] == "python"

    resultats = await service.rechercher("Quel port ouvrir dans le pare-feu ?", limite=1)
    assert resultats and resultats[0]["filename"] == "reseau.md"
    assert 0.0 <= resultats[0]["score"] <= 1.0
    await db.close()


# --------------------------------------------------------------------------- #
#  Dossier surveillé
# --------------------------------------------------------------------------- #
async def test_scan_du_dossier_surveille(rag, tmp_path):
    dossier = tmp_path / "documents"
    (dossier / "sous-dossier").mkdir(parents=True)
    (dossier / "note.md").write_text("# Note\n\nUn contenu assez long pour l'index.\n")
    (dossier / "sous-dossier" / "autre.txt").write_text(
        "Un second document, dans un sous-dossier, avec du texte."
    )
    (dossier / "image.png").write_bytes(b"\x89PNG")

    resume = await rag.scanner()
    assert resume["existe"] is True
    assert resume["ajoutes"] == 2
    assert resume["ignores"] == 1        # le PNG n'est pas un format reconnu

    # Un second scan ne réindexe pas ce qui n'a pas changé.
    second = await rag.scanner()
    assert second["ajoutes"] == 0 and second["inchanges"] == 2

    documents = await rag.depot.documents()
    assert {d["source"] for d in documents} == {"dossier"}
    assert any(d["path"].endswith("autre.txt") for d in documents)


async def test_dossier_absent_est_signale(rag, tmp_path):
    rag.settings.documents_dir = str(tmp_path / "inexistant")
    resume = await rag.scanner()
    assert resume["existe"] is False
    assert "montez-le" in resume["message"]


# --------------------------------------------------------------------------- #
#  Divers
# --------------------------------------------------------------------------- #
def test_empreinte_et_cosinus():
    assert empreinte(b"abc") == empreinte(b"abc")
    assert empreinte(b"abc") != empreinte(b"abd")
    assert cosinus([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosinus([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosinus([0, 0], [1, 0]) == 0.0


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
def test_routes_documents(tmp_path):
    import app.main as principal

    principal.settings.data_dir = str(tmp_path)
    principal.settings.documents_dir = str(tmp_path / "documents")

    with TestClient(app) as client:
        app.state.rag.client = EmbeddingsFactices()

        etat = client.get("/api/documents").json()
        assert etat["disponible"] is True
        assert etat["documents"] == []
        assert ".pdf" in etat["formats"]

        depot = client.post(
            "/api/documents",
            files={"files": ("note.md", b"# Titre\n\nUn contenu assez long pour l'index.\n",
                             "text/markdown")},
        )
        assert depot.status_code == 201
        document = depot.json()["resultats"][0]
        assert document["status"] == "indexe"

        recherche = client.get("/api/documents/recherche", params={"q": "contenu"})
        assert recherche.status_code == 200
        assert recherche.json()["resultats"][0]["filename"] == "note.md"

        assert client.delete(f"/api/documents/{document['id']}").status_code == 204
        assert client.get("/api/documents").json()["documents"] == []
