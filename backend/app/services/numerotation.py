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

Le numéro est attribué à la SOUTENANCE : un travail en préparation n'en
a pas (generer_numero renvoie None). Il le reçoit à la synchronisation
qui suit le passage au statut « soutenu ». La lettre de statut vaut donc
toujours S pour les nouveaux numéros ; elle est conservée pour que les
numéros déjà attribués restent valides.

Le code de l'établissement (2 caractères) est géré depuis
l'administration (etablissements.code_numero). La table
CODES_ETABLISSEMENTS ne sert plus que de valeur initiale.

C'est le format des numéros déjà attribués en production. La version
précédente de ce fichier produisait « SC-UC-T-S-2024-0012-47 » (22
caractères) : refusé par la colonne numero_national (20 caractères), et
incohérent avec les numéros existants.
"""

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, DocumentRetire, Etablissement, NumerotationCompteur


# Valeurs initiales, reprises en base au démarrage (core/schema.py)
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
MOTIF_CODE_NUMERO = re.compile(r"^[A-Z0-9]{2}$")


def _calculer_cle(numero_partiel: str) -> str:
    """
    Clé de contrôle modulo 97 : 98 - (N mod 97), N étant la suite des
    chiffres du numéro partiel (année + ordre).
    """
    chiffres = "".join(c for c in numero_partiel if c.isdigit())
    if not chiffres:
        return "00"
    return str(98 - (int(chiffres) % 97)).zfill(2)


def code_par_defaut(etablissement_code: str) -> str:
    """Proposition pour un nouvel établissement (modifiable ensuite)."""
    code = CODES_ETABLISSEMENTS.get(etablissement_code)
    if code:
        return code
    return re.sub(r"[^A-Z0-9]", "", (etablissement_code or "").upper())[:2].ljust(2, "X")


def code_etablissement(etablissement_code: str, db: Session = None) -> str:
    """Code à 2 caractères de l'établissement dans le numéro national."""
    if db is not None:
        etab = db.query(Etablissement).filter(Etablissement.code == etablissement_code).first()
        if etab is not None and etab.code_numero:
            return etab.code_numero
    return code_par_defaut(etablissement_code)


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
    motif = f"{prefixe}_{int(annee):04d}%"
    # Les numéros des documents retirés comptent aussi : un numéro
    # national n'est jamais réattribué, même à un autre travail.
    numeros = (
        db.query(Document.numero_national)
        .filter(Document.numero_national.like(motif))
        .all()
    ) + (
        db.query(DocumentRetire.numero_national)
        .filter(DocumentRetire.numero_national.like(motif))
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
):
    """
    Génère un numéro national unique et incrémente le compteur
    (établissement, type, année). Renvoie None pour un travail qui n'est
    pas encore soutenu : il ne consomme aucun rang.
    """
    if statut != "soutenu":
        return None
    if annee is None:
        annee = datetime.now().year

    code_etab = code_etablissement(etablissement_code, db)

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
