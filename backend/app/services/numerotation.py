"""
ScholarSync — Service de numérotation nationale

Format : SC{CODE}{TYPE}{STATUT}{ANNEE}{ORDRE}{CLE} — 16 caractères, sans séparateur
    SC      préfixe fixe                       2
    CODE    code établissement                 2   (UC = UCAD…)
    TYPE    T = thèse, M = mémoire             1
    STATUT  S = soutenu, P = en préparation    1
    ANNEE   année de soutenance                4
    ORDRE   rang dans (établissement, type, année)  4
    CLE     clé de contrôle modulo 97          2

Exemple : SCUCTS2016000192

C'est le format des numéros déjà attribués en production. La version
précédente de ce fichier produisait « SC-UC-T-S-2024-0012-47 » (22
caractères) : refusé par la colonne numero_national (20 caractères), et
incohérent avec les numéros existants.
"""

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, NumerotationCompteur


CODES_ETABLISSEMENTS = {
    "UCAD":  "UC",
    "UGB":   "UG",
    "UADB":  "UA",
    "UASZ":  "US",
    "UIDT":  "UI",
    "UNCHK": "UN",
}

CODES_TYPES = {
    "these":   "T",
    "memoire": "M",
}

CODES_STATUTS = {
    "soutenu":         "S",
    "en_preparation":  "P",
}

LONGUEUR = 16
MOTIF = re.compile(r"^SC[A-Z0-9]{2}[TMX][SPX]\d{4}\d{4}\d{2}$")


def _calculer_cle(numero_partiel: str) -> str:
    """
    Clé de contrôle modulo 97 : 98 - (N mod 97), N étant la suite des
    chiffres du numéro partiel (année + ordre).
    """
    chiffres = "".join(c for c in numero_partiel if c.isdigit())
    if not chiffres:
        return "00"
    return str(98 - (int(chiffres) % 97)).zfill(2)


def code_etablissement(etablissement_code: str) -> str:
    code = CODES_ETABLISSEMENTS.get(etablissement_code)
    if code:
        return code
    return (etablissement_code or "XX")[:2].upper().ljust(2, "X")


def composer_numero(code_etab: str, type_doc: str, statut: str,
                    annee: int, ordre: int) -> str:
    """Assemble un numéro complet, clé comprise. Sans accès à la base."""
    partiel = (
        f"SC{code_etab}"
        f"{CODES_TYPES.get(type_doc, 'X')}"
        f"{CODES_STATUTS.get(statut, 'X')}"
        f"{int(annee):04d}{int(ordre):04d}"
    )
    return partiel + _calculer_cle(partiel)


def _plus_grand_ordre_existant(db: Session, code_etab: str, type_doc: str,
                               annee: int) -> int:
    """
    Plus grand rang déjà attribué pour (établissement, type, année).

    Le compteur peut manquer alors que des numéros existent (base
    restaurée, compteurs vidés, documents créés par une version
    antérieure) : repartir de 1 produirait un doublon et ferait échouer
    l'insertion. On relit donc les numéros déjà en base.
    """
    prefixe = f"SC{code_etab}{CODES_TYPES.get(type_doc, 'X')}"
    numeros = (
        db.query(Document.numero_national)
        .filter(Document.numero_national.like(f"{prefixe}_{int(annee):04d}%"))
        .all()
    )
    ordres = [
        int(n[0][10:14]) for n in numeros
        if n[0] and len(n[0].strip()) == LONGUEUR and n[0][10:14].isdigit()
    ]
    return max(ordres, default=0)


def generer_numero(
    db: Session,
    etablissement_code: str,
    type_doc: str,
    statut: str,
    annee: int = None,
) -> str:
    """
    Génère un numéro national unique et incrémente le compteur
    (établissement, type, année).
    """
    if annee is None:
        annee = datetime.now().year

    code_etab = code_etablissement(etablissement_code)

    compteur = (
        db.query(NumerotationCompteur)
        .filter_by(etablissement_code=etablissement_code, type=type_doc, annee=annee)
        .with_for_update()
        .first()
    )
    existant = _plus_grand_ordre_existant(db, code_etab, type_doc, annee)

    if compteur is None:
        compteur = NumerotationCompteur(
            etablissement_code=etablissement_code,
            type=type_doc,
            annee=annee,
            compteur=existant + 1,
        )
        db.add(compteur)
    else:
        compteur.compteur = max(compteur.compteur or 0, existant) + 1

    db.flush()
    return composer_numero(code_etab, type_doc, statut, annee, compteur.compteur)


def valider_numero(numero: str) -> bool:
    """Vérifie le format et la clé de contrôle d'un numéro national."""
    if not numero:
        return False
    numero = numero.strip().upper()
    if not MOTIF.match(numero):
        return False
    return _calculer_cle(numero[:-2]) == numero[-2:]
