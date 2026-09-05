"""Découpage des documents en fragments indexables.

Le découpage décide de la qualité du RAG plus que le modèle lui-même : un
fragment coupé au milieu d'une phrase produit un embedding qui ne représente
rien, et une réponse qui cite un morceau incompréhensible.

Trois règles ici :

1. on ne coupe jamais au milieu d'une phrase si on peut l'éviter ;
2. les fragments se chevauchent, pour qu'une idée à cheval sur deux morceaux
   reste retrouvable ;
3. la provenance (page, titre de section) suit chaque fragment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extraction import Fragment

#: Taille visée, en caractères. On raisonne en caractères et non en jetons :
#: c'est approximatif, mais cela évite d'embarquer un tokeniseur par modèle.
TAILLE_CIBLE = 1000
CHEVAUCHEMENT = 150
TAILLE_MINIMALE = 80

_FIN_DE_PHRASE = re.compile(r"(?<=[.!?…:;])\s+|\n\n+")


@dataclass
class Morceau:
    """Un fragment prêt à être vectorisé."""

    contenu: str
    ordinal: int
    page: int | None = None
    titre: str | None = None


def _phrases(texte: str) -> list[str]:
    parties = [p.strip() for p in _FIN_DE_PHRASE.split(texte) if p and p.strip()]
    return parties or ([texte.strip()] if texte.strip() else [])


def _decouper_texte(
    texte: str, taille: int, chevauchement: int
) -> list[str]:
    """Regroupe les phrases jusqu'à la taille visée, avec chevauchement."""
    phrases = _phrases(texte)
    morceaux: list[str] = []
    courant: list[str] = []
    longueur = 0

    for phrase in phrases:
        # Une phrase plus longue que la cible est coupée durement : rare, mais
        # cela arrive avec des tableaux aplatis ou du texte sans ponctuation.
        if len(phrase) > taille:
            if courant:
                morceaux.append(" ".join(courant))
                courant, longueur = [], 0
            for debut in range(0, len(phrase), taille):
                morceaux.append(phrase[debut : debut + taille])
            continue

        if longueur + len(phrase) + 1 > taille and courant:
            morceaux.append(" ".join(courant))
            # Report des dernières phrases pour créer le chevauchement.
            report: list[str] = []
            report_longueur = 0
            for precedente in reversed(courant):
                if report_longueur + len(precedente) > chevauchement:
                    break
                report.insert(0, precedente)
                report_longueur += len(precedente) + 1
            courant = report
            longueur = report_longueur

        courant.append(phrase)
        longueur += len(phrase) + 1

    if courant:
        morceaux.append(" ".join(courant))
    return [m.strip() for m in morceaux if m.strip()]


def decouper(
    fragments: list[Fragment],
    taille: int = TAILLE_CIBLE,
    chevauchement: int = CHEVAUCHEMENT,
) -> list[Morceau]:
    """Transforme les fragments extraits en morceaux indexables."""
    morceaux: list[Morceau] = []
    ordinal = 0

    for fragment in fragments:
        for contenu in _decouper_texte(fragment.texte, taille, chevauchement):
            if len(contenu) < TAILLE_MINIMALE and morceaux:
                # Un résidu trop court n'a pas d'embedding utile : on le
                # rattache au morceau précédent plutôt que de l'indexer seul.
                precedent = morceaux[-1]
                if len(precedent.contenu) + len(contenu) <= taille * 1.4:
                    precedent.contenu = f"{precedent.contenu} {contenu}"
                    continue
            morceaux.append(
                Morceau(
                    contenu=contenu,
                    ordinal=ordinal,
                    page=fragment.page,
                    titre=fragment.titre,
                )
            )
            ordinal += 1

    return morceaux


def contextualiser(morceau: Morceau, nom_document: str) -> str:
    """Texte réellement vectorisé : le contenu précédé de sa provenance.

    Sans cet en-tête, un fragment parlant de « la version 3 » ne se rattache à
    rien ; avec le nom du document et le titre de section, il devient
    retrouvable par une question qui nomme le sujet sans reprendre ses mots.
    """
    entete = [nom_document]
    if morceau.titre:
        entete.append(morceau.titre)
    if morceau.page:
        entete.append(f"page {morceau.page}")
    return f"[{' — '.join(entete)}]\n{morceau.contenu}"
