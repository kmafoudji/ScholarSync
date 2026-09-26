"""
Référentiel des établissements et de leur code de numérotation.

Le code de 2 caractères qui figure dans le numéro national
(SN<code>T2016000192) suit une règle : le 1er caractère donne la famille
d'établissement, le 2e l'établissement.

    U   universités publiques — 2e lettre : la ville, ou le nom quand
        la ville est déjà prise (UC Dakar/UCAD, UG Saint-Louis…)
    E   grandes écoles publiques hors université délivrant master ou
        doctorat (EP : École polytechnique de Thiès)
    R   établissements régionaux ou inter-États installés au pays
    P   établissements privés, dans l'ordre d'adhésion : P1…P9, puis PA…PZ

Une école interne à une université (l'ESP à l'UCAD) n'a pas de code à
elle : ses travaux portent celui de l'université. Un code attribué n'est
jamais réutilisé, même si l'établissement ferme ou fusionne.

`installer` inscrit ces établissements une seule fois (drapeau dans
`parametres`) : un établissement supprimé ensuite par l'administration
ne revient pas au redémarrage. Ils sont créés INACTIFS — absents de la
page publique « Établissements » tant qu'ils n'ont pas rejoint la
plateforme ; le super administrateur les active à leur adhésion.

Trois universités avaient reçu un code provisoire (UA, UI, US) : il
passe à la règle (UB, UT, UZ) seulement si aucun numéro national n'a
encore été attribué avec lui — un numéro ne change jamais.
"""
from __future__ import annotations

import logging
import string

from sqlalchemy.orm import Session

from app.models import Document, DocumentRetire, Etablissement, Parametre

logger = logging.getLogger("scholarsync")

DRAPEAU = "referentiel_etablissements"
VERSION = "1"

FAMILLES = [
    ("U", "Universités publiques", "la ville, ou le nom si la ville est déjà prise"),
    ("E", "Grandes écoles publiques", "hors université, délivrant master ou doctorat"),
    ("R", "Établissements régionaux ou inter-États", "installés au Sénégal"),
    ("P", "Établissements privés", "dans l'ordre d'adhésion : P1…P9, puis PA…PZ"),
]

# (code, nom, ville, code de numérotation)
ETABLISSEMENTS = [
    ("UCAD",   "Université Cheikh Anta Diop de Dakar",                 "Dakar",       "UC"),
    ("UGB",    "Université Gaston Berger de Saint-Louis",              "Saint-Louis", "UG"),
    ("UADB",   "Université Alioune Diop de Bambey",                    "Bambey",      "UB"),
    ("UIDT",   "Université Iba Der Thiam de Thiès",                    "Thiès",       "UT"),
    ("UASZ",   "Université Assane Seck de Ziguinchor",                 "Ziguinchor",  "UZ"),
    ("UNCHK",  "Université numérique Cheikh Hamidou Kane",             None,          "UN"),
    ("USSEIN", "Université du Sine Saloum El Hâdj Ibrahima Niass",     "Kaolack",     "US"),
    ("UAM",    "Université Amadou Mahtar Mbow",                        "Diamniadio",  "UD"),
    ("USO",    "Université du Sénégal oriental",                       "Tambacounda", "UO"),
    ("USN",    "Université Souleymane Niang de Matam",                 "Matam",       "UM"),
    ("EPT",    "École polytechnique de Thiès",                         "Thiès",       "EP"),
    ("EISMV",  "École inter-États des sciences et médecine vétérinaires", "Dakar",    "RV"),
    ("CESAG",  "Centre africain d'études supérieures en gestion",      "Dakar",       "RG"),
]

# Codes provisoires remplacés par la règle, dans cet ordre (UZ libère US
# avant que l'USSEIN ne le prenne).
RECODAGES = [("UASZ", "US", "UZ"), ("UADB", "UA", "UB"), ("UIDT", "UI", "UT")]

SUITE_PRIVES = [f"P{c}" for c in string.digits[1:] + string.ascii_uppercase]


def _a_des_numeros(db: Session, code: str) -> bool:
    return bool(
        db.query(Document.id).filter(Document.etablissement_code == code,
                                     Document.numero_national.isnot(None)).first()
        or db.query(DocumentRetire.id).filter(DocumentRetire.etablissement_code == code,
                                              DocumentRetire.numero_national.isnot(None)).first()
    )


def _pris(db: Session, code_numero: str, sauf_code: str = None) -> bool:
    q = db.query(Etablissement.id).filter(Etablissement.code_numero == code_numero)
    if sauf_code:
        q = q.filter(Etablissement.code != sauf_code)
    return q.first() is not None


def installer(db: Session) -> dict:
    """Inscrit le référentiel, une seule fois. Renvoie ce qui a été fait."""
    drapeau = db.get(Parametre, DRAPEAU)
    if drapeau is not None and drapeau.valeur == VERSION:
        return {}
    bilan = {"recodes": [], "ajoutes": [], "ignores": []}

    for code, ancien, nouveau in RECODAGES:
        etab = db.query(Etablissement).filter(Etablissement.code == code).first()
        if etab is None or etab.code_numero != ancien:
            continue
        if _a_des_numeros(db, code):
            bilan["ignores"].append(f"{code} garde {ancien} (numéros déjà attribués)")
        elif _pris(db, nouveau, sauf_code=code):
            bilan["ignores"].append(f"{code} garde {ancien} ({nouveau} déjà pris)")
        else:
            etab.code_numero = nouveau
            db.flush()
            bilan["recodes"].append(f"{code} : {ancien} → {nouveau}")

    for code, nom, ville, code_numero in ETABLISSEMENTS:
        if db.query(Etablissement.id).filter(Etablissement.code == code).first():
            continue
        if _pris(db, code_numero):
            bilan["ignores"].append(f"{code} non ajouté ({code_numero} déjà pris)")
            continue
        db.add(Etablissement(code=code, nom=nom, ville=ville,
                             pays="Sénégal", code_numero=code_numero, actif=False))
        db.flush()
        bilan["ajoutes"].append(f"{code} ({code_numero})")

    if drapeau is None:
        db.add(Parametre(cle=DRAPEAU, valeur=VERSION, type="text"))
    else:
        drapeau.valeur = VERSION
    db.commit()
    for cle, lignes in bilan.items():
        if lignes:
            logger.info("Référentiel des établissements — %s : %s", cle, " ; ".join(lignes))
    return bilan


def prochain_code_prive(db: Session):
    """Premier code de la famille P encore libre (P1, P2…), ou None."""
    pris = {c for (c,) in db.query(Etablissement.code_numero)
            .filter(Etablissement.code_numero.like("P%"))}
    return next((c for c in SUITE_PRIVES if c not in pris), None)
