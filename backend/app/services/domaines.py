"""
Domaines de formation REESAO et classement des documents.

Chaque document reçoit au plus un domaine REESAO (documents.domaine_reesao),
décidé dans cet ordre :

  1. le choix fait à la main par l'établissement (documents.domaine_manuel),
     jamais écrasé par une synchronisation ;
  2. la métadonnée de la notice (documents.domaine : marqueur Zotero
     « domaine: … », colonne d'import, sujet OAI) : code court (SEG),
     intitulé, synonyme connu, ou correspondance enregistrée par le super
     administrateur (« Hydrologie » → ST) ;
  3. la faculté ou l'école doctorale (documents.sous_entite_nom), rattachée
     une fois pour toutes à un domaine par l'établissement — sauf si elle
     est déclarée « Plusieurs domaines » ;
  4. sinon : non classé (NULL), visible dans l'administration.

La valeur libre d'origine (documents.domaine) est conservée : c'est la
discipline, plus fine, affichée sur la fiche et cherchable.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

MULTI = "MULTI"

# code → (intitulé français, anglais, portugais)
DOMAINES = {
    "SS":   ("Sciences de la santé", "Health sciences", "Ciências da saúde"),
    "ST":   ("Sciences et technologies", "Science and technology", "Ciências e tecnologias"),
    "SJPA": ("Sciences juridiques, politiques et de l'administration",
             "Law, political science and public administration",
             "Ciências jurídicas, políticas e da administração"),
    "SEG":  ("Sciences économiques et de gestion", "Economics and management",
             "Ciências económicas e de gestão"),
    "SHS":  ("Sciences de l'homme et de la société", "Humanities and social sciences",
             "Ciências humanas e sociais"),
    "LLA":  ("Lettres, langues et arts", "Literature, languages and arts", "Letras, línguas e artes"),
    "SA":   ("Sciences agronomiques", "Agricultural sciences", "Ciências agronómicas"),
    "SEF":  ("Sciences de l'éducation et de la formation", "Education and training sciences",
             "Ciências da educação e da formação"),
}

# Intitulés courts, pour les listes étroites de l'administration
COURTS = {
    "SS": "Santé", "ST": "Sciences et technologies", "SJPA": "Droit et science politique",
    "SEG": "Économie et gestion", "SHS": "Sc. humaines et sociales",
    "LLA": "Lettres, langues, arts", "SA": "Agronomie", "SEF": "Éducation et formation",
}

# Mots qui désignent un domaine, dans un nom de faculté ou une métadonnée
# (comparés sans accents ni majuscules, mot entier ou début de mot).
MOTS = {
    "SS": ["sante", "medecine", "medical", "pharmac", "odontolog", "chirurg", "infirmi",
           "sage-femme", "sages-femmes", "veterinaire", "epidemiolog", "paludisme",
           "biologie medicale", "fmpo", "nutrition"],
    "ST": ["sciences et techniques", "sciences et technologies", "technolog", "informatique",
           "mathemati", "physique", "chimie", "ingenieri", "polytechni", "genie", "numerique",
           "geologie", "hydrolog", "biologie vegetale", "biologie animale", "sciences exactes",
           "sciences appliquees", "electroni", "electrotechni", "energie", "fst"],
    "SJPA": ["droit", "juridique", "science politique", "sciences politiques", "administration publique",
             "fsjp", "relations internationales"],
    "SEG": ["econom", "gestion", "finance", "comptab", "management", "commerce", "marketing",
            "faseg", "banque", "assurance"],
    "SHS": ["sciences humaines", "sciences sociales", "sociolog", "histoire", "geographi",
            "philosoph", "psycholog", "anthropolog", "demograph", "sciences de l'homme"],
    "LLA": ["lettres", "langues", "linguisti", "litterat", "arts", "anglais", "arabe",
            "francais", "espagnol", "allemand", "portugais", "traduction"],
    "SA": ["agronom", "agricult", "elevage", "halieuti", "peche", "forestier", "foresterie",
           "agroalimentaire", "ensa", "productions animales", "productions vegetales", "sols"],
    "SEF": ["education", "formation des enseignants", "pedagog", "enseignement", "fastef",
            "didactique"],
}


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' -]", " ", texte).split())


_INTITULES = {normaliser(v[0]): k for k, v in DOMAINES.items()}
_MOTIFS = {code: re.compile(r"(?:^|[\s'(-])(?:" + "|".join(re.escape(m) for m in mots) + r")")
           for code, mots in MOTS.items()}


def domaines_evoques(texte: str) -> set:
    """Codes des domaines dont un mot figure dans `texte`."""
    t = normaliser(texte)
    if not t:
        return set()
    if t in _INTITULES:
        return {_INTITULES[t]}
    return {code for code, motif in _MOTIFS.items() if motif.search(t)}


def proposer(nom_entite: str):
    """Proposition pour une faculté : un code, MULTI, ou None."""
    codes = domaines_evoques(nom_entite)
    if len(codes) == 1:
        return next(iter(codes))
    return MULTI if codes else None


def depuis_metadonnee(valeur: str, correspondances: dict):
    """Code REESAO lu dans une métadonnée, ou None si elle ne suffit pas."""
    if not valeur:
        return None
    brut = valeur.strip()
    if brut.upper() in DOMAINES:
        return brut.upper()
    t = normaliser(brut)
    if t in correspondances:
        return correspondances[t]
    codes = domaines_evoques(brut)
    return next(iter(codes)) if len(codes) == 1 else None


def libelle(code: str, langue: str = "fr") -> str:
    if code not in DOMAINES:
        return code or ""
    fr, en, pt = DOMAINES[code]
    return {"en": en, "pt": pt}.get(langue, fr)


# ── Application ────────────────────────────────────────────────────

def _tables(db: Session):
    from app.models import CorrespondanceDomaine, RattachementDomaine
    correspondances = {c.valeur: c.domaine for c in db.query(CorrespondanceDomaine)}
    rattachements = {(r.etablissement_code, r.sous_entite_nom): r.domaine
                     for r in db.query(RattachementDomaine)}
    return correspondances, rattachements


def resoudre(doc, correspondances: dict, rattachements: dict):
    """(code ou None, source) pour un document."""
    if doc.domaine_manuel in DOMAINES:
        return doc.domaine_manuel, "manuel"
    code = depuis_metadonnee(doc.domaine, correspondances)
    if code:
        return code, "metadonnee"
    code = rattachements.get((doc.etablissement_code, doc.sous_entite_nom))
    if code in DOMAINES:
        return code, "faculte"
    return None, None


def recalculer(db: Session, requete=None) -> int:
    """Recalcule documents.domaine_reesao ; renvoie le nombre de changements.

    `requete` limite le calcul (les documents d'un établissement, d'une
    source) ; sans elle, tout le catalogue est repris.
    """
    from app.models import Document
    correspondances, rattachements = _tables(db)
    requete = requete if requete is not None else db.query(Document)
    changes = 0
    for doc in requete.yield_per(500):
        code, _ = resoudre(doc, correspondances, rattachements)
        if doc.domaine_reesao != code:
            doc.domaine_reesao = code
            changes += 1
    db.flush()
    return changes


def entites_etablissement(db: Session, code: str) -> list:
    """Facultés et écoles doctorales d'un établissement, avec leur nombre
    de documents, leur rattachement et la proposition automatique."""
    from sqlalchemy import func
    from app.models import Document, RattachementDomaine
    comptes = dict(
        db.query(Document.sous_entite_nom, func.count())
        .filter(Document.etablissement_code == code, Document.sous_entite_nom.isnot(None))
        .group_by(Document.sous_entite_nom).all()
    )
    rattaches = {r.sous_entite_nom: r.domaine for r in
                 db.query(RattachementDomaine).filter(RattachementDomaine.etablissement_code == code)}
    noms = sorted(set(comptes) | set(rattaches), key=lambda n: normaliser(n))
    return [{
        "nom": n,
        "documents": comptes.get(n, 0),
        "domaine": rattaches.get(n),
        "proposition": proposer(n),
    } for n in noms]


def valeurs_a_classer(db: Session, code: str = None, limite: int = 200) -> list:
    """Valeurs de métadonnée non reconnues, portées par des documents restés
    non classés : ce que le super administrateur peut rattacher."""
    from sqlalchemy import func
    from app.models import Document
    q = (db.query(Document.domaine, func.count())
         .filter(Document.domaine_reesao.is_(None), Document.domaine.isnot(None),
                 Document.domaine != ""))
    if code:
        q = q.filter(Document.etablissement_code == code)
    lignes = q.group_by(Document.domaine).order_by(func.count().desc()).limit(limite).all()
    return [{"valeur": v, "documents": n} for v, n in lignes]
