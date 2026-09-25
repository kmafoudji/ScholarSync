"""
ScholarSync — Service de numérotation nationale

Format : SN{CODE}{TYPE}{ANNEE}{ORDRE}{CLE} — 15 caractères, sans séparateur
    SN      préfixe fixe (Sénégal)             2
    CODE    code établissement                 2   (UC = UCAD…)
    TYPE    T = thèse, M = mémoire             1
    ANNEE   année de soutenance                4
    ORDRE   rang dans (établissement, type, année)  4
    CLE     clé de contrôle modulo 97          2

Exemple : SNUCT2016000192

Le numéro est attribué à la SOUTENANCE : un travail en préparation n'en
a pas (generer_numero renvoie None). Il le reçoit à la synchronisation
qui suit le passage au statut « soutenu ».

Jusqu'en septembre 2026, le numéro commençait par SC et portait en 6e
position une lettre de statut (S soutenu, P en préparation) :
SCUCTS2016000192, 16 caractères. Depuis que seuls les travaux soutenus
sont numérotés, la lettre valait toujours S ; elle a été retirée, et le
préfixe est devenu SN, avant l'ouverture au public. Les numéros déjà
attribués ont été convertis une fois au démarrage (core/schema.py) :
même rang, même clé, puisque la clé ne porte que sur les chiffres. `normaliser` accepte
encore l'ancienne forme, pour qu'un numéro noté avant la conversion
retrouve son document.

Le code de l'établissement (2 caractères) est géré depuis
l'administration (etablissements.code_numero). La table
CODES_ETABLISSEMENTS ne sert plus que de valeur initiale.
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

LONGUEUR = 15
PREFIXE = "SN"
MOTIF = re.compile(rf"^{PREFIXE}[A-Z0-9]{{2}}[TMX]\d{{4}}\d{{4}}\d{{2}}$")
# Anciennes formes (voir l'en-tête) : préfixe SC, avec ou sans la lettre
# de statut
MOTIF_ANCIEN = re.compile(r"^SC([A-Z0-9]{2}[TMX])[SP]?(\d{10})$")
# Positions dans le numéro
ORDRE = slice(9, 13)
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


def composer_numero(code_etab: str, type_doc: str, annee: int, ordre: int) -> str:
    """Assemble un numéro complet, clé comprise. Sans accès à la base."""
    partiel = (
        f"{PREFIXE}{code_etab}"
        f"{CODES_TYPES.get(type_doc, 'X')}"
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
    prefixe = f"{PREFIXE}{code_etab}{CODES_TYPES.get(type_doc, 'X')}"
    motif = f"{prefixe}{int(annee):04d}%"
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
        int(n[0][ORDRE]) for n in numeros
        if n[0] and len(n[0].strip()) == LONGUEUR and n[0][ORDRE].isdigit()
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
    return composer_numero(code_etab, type_doc, annee, compteur.compteur)


def normaliser(numero: str) -> str:
    """Forme actuelle d'un numéro saisi : majuscules, sans espaces ni
    tirets ; un numéro de l'ancienne forme (SC…) ramené à la forme SN…."""
    brut = re.sub(r"[\s-]", "", numero or "").upper()
    ancien = MOTIF_ANCIEN.match(brut)
    return PREFIXE + ancien.group(1) + ancien.group(2) if ancien else brut


def valider_numero(numero: str) -> bool:
    """Vérifie le format et la clé de contrôle d'un numéro national."""
    if not numero:
        return False
    numero = numero.strip().upper()
    if not MOTIF.match(numero):
        return False
    return _calculer_cle(numero[:-2]) == numero[-2:]
