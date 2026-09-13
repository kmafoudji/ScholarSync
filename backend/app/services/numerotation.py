"""
ScholarSync — Service de numérotation nationale
Format : SC-{CODE}-{TYPE}-{STATUT}-{ANNEE}-{ORDRE}-{CLE}
Exemple : SC-UC-T-S-2024-0012-47
"""

from datetime import datetime
from sqlalchemy.orm import Session


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


def _calculer_cle(numero_partiel: str) -> str:
    """
    Calcule la clé de contrôle modulo 97.
    On extrait les chiffres du numéro partiel et on calcule 98 - (N mod 97).
    """
    chiffres = "".join(c for c in numero_partiel if c.isdigit())
    if not chiffres:
        return "00"
    cle = 98 - (int(chiffres) % 97)
    return str(cle).zfill(2)


def generer_numero(
    db: Session,
    etablissement_code: str,
    type_doc: str,
    statut: str,
    annee: int = None,
) -> str:
    """
    Génère un numéro national unique pour un document.
    Incrémente le compteur (etablissement_code, type, annee).
    """
    if annee is None:
        annee = datetime.now().year

    code_etab = CODES_ETABLISSEMENTS.get(etablissement_code, etablissement_code[:2].upper())
    code_type = CODES_TYPES.get(type_doc, "X")
    code_statut = CODES_STATUTS.get(statut, "X")

    # Incrémenter le compteur
    from app.models.numerotation import NumerotationCompteur
    compteur = db.query(NumerotationCompteur).filter_by(
        etablissement_code=etablissement_code,
        type=type_doc,
        annee=annee,
    ).with_for_update().first()

    if compteur is None:
        compteur = NumerotationCompteur(
            etablissement_code=etablissement_code,
            type=type_doc,
            annee=annee,
            compteur=1,
        )
        db.add(compteur)
    else:
        compteur.compteur += 1

    db.flush()
    ordre = str(compteur.compteur).zfill(4)

    # Construire le numéro partiel
    partiel = f"SC-{code_etab}-{code_type}-{code_statut}-{annee}-{ordre}"

    # Calculer la clé
    cle = _calculer_cle(partiel)

    return f"{partiel}-{cle}"


def valider_numero(numero: str) -> bool:
    """Valide la clé de contrôle d'un numéro national."""
    try:
        parties = numero.split("-")
        if len(parties) != 7:
            return False
        cle_attendue = parties[-1]
        partiel = "-".join(parties[:-1])
        return _calculer_cle(partiel) == cle_attendue
    except Exception:
        return False
