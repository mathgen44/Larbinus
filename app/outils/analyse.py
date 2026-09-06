"""Lecture des blocs d'action émis par le modèle.

Format attendu dans la réponse :

    ```larbinus:ssh
    hote: beast
    commande: docker ps
    ```

L'analyse est délibérément tolérante — accents ignorés sur les clés, espaces
libres, guillemets superflus retirés. Un modèle de 7 milliards de paramètres
respecte un format « à peu près » ; refuser sa proposition pour une majuscule
mal placée rendrait la fonctionnalité inutilisable.
"""

from __future__ import annotations

import re
import unicodedata

#: Ouvre un bloc ```larbinus:<outil> et capture tout jusqu'à la fermeture.
_BLOC = re.compile(
    r"```[ \t]*larbinus[ \t]*:[ \t]*([\w-]+)[ \t]*\n(.*?)(?:```|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _normaliser_cle(cle: str) -> str:
    sans_accent = unicodedata.normalize("NFKD", cle).encode("ascii", "ignore").decode()
    return sans_accent.strip().lower().replace(" ", "_")


def _nettoyer_valeur(valeur: str) -> str:
    valeur = valeur.strip()
    # Un modèle entoure volontiers sa valeur de guillemets ; les garder ferait
    # exécuter une commande littéralement guillemetée.
    if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
        valeur = valeur[1:-1]
    return valeur.strip()


def analyser_bloc(contenu: str) -> dict[str, str]:
    """Transforme les lignes `clé: valeur` d'un bloc en dictionnaire.

    Le découpage se fait sur le **premier** deux-points : une commande shell en
    contient souvent d'autres (`--format '{{.Names}}: {{.Status}}'`).
    """
    parametres: dict[str, str] = {}
    for ligne in contenu.split("\n"):
        if not ligne.strip() or ":" not in ligne:
            continue
        cle, _, valeur = ligne.partition(":")
        cle = _normaliser_cle(cle)
        if cle:
            parametres[cle] = _nettoyer_valeur(valeur)
    return parametres


def extraire_blocs(texte: str) -> list[tuple[str, dict[str, str], str]]:
    """Renvoie les triplets (nom d'outil, paramètres, bloc brut) d'une réponse."""
    blocs = []
    for correspondance in _BLOC.finditer(texte):
        outil = correspondance.group(1).strip().lower()
        contenu = correspondance.group(2)
        blocs.append((outil, analyser_bloc(contenu), correspondance.group(0)))
    return blocs


def retirer_blocs(texte: str) -> str:
    """Réponse débarrassée de ses blocs d'action.

    Utilisé pour l'historique : réinjecter les blocs au tour suivant amènerait
    le modèle à croire qu'il doit les reproduire, et la même commande partirait
    en boucle.
    """
    return _BLOC.sub("", texte).strip()
