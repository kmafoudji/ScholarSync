"""
Accès effectif aux documents : application des règles d'accès.

Les règles (table acces_exceptions) étaient enregistrées depuis la page
« Contrôle d'accès » mais n'étaient lues nulle part : restreindre UCAD
ou la collection Thèses ne changeait rien sur le portail.

Principe : la règle la plus fine l'emporte.

    document  >  sous-collection  >  collection  >  établissement  >  public

- document        : référence = identifiant (UUID) du document ;
- sous_collection : nom de la faculté ou de l'école doctorale, seul
                    (« Faculté des Sciences ») ou préfixé du code
                    (« UCAD/Faculté des Sciences ») pour lever l'ambiguïté
                    entre universités ; la forme préfixée prime ;
- collection      : « Thèses » ou « Mémoires », seul ou préfixé
                    (« UCAD/Thèses ») ;
- etablissement   : code (« UCAD »).

L'accès effectif est ensuite ÉCRIT dans documents.acces. Le portail,
les facettes, les exports et les citations lisent ce seul champ : pas de
calcul à chaque affichage, et aucune page ne peut oublier une règle.
Il est recalculé quand une règle change et après chaque synchronisation.

« Restreint » signifie : la notice reste signalée (c'est la mission du
portail), mais le lien vers le texte intégral n'est publié nulle part —
ni sur la fiche, ni dans les exports, ni dans les citations.
"""
from __future__ import annotations

import unicodedata

from sqlalchemy.orm import Session

from app.models import AccesException, Document

PUBLIC, RESTREINT = "public", "restreint"
NIVEAUX = ("etablissement", "collection", "sous_collection", "document")

# Écritures acceptées pour les deux collections
COLLECTIONS = {
    "these": "these", "theses": "these", "thèse": "these", "thèses": "these",
    "memoire": "memoire", "memoires": "memoire", "mémoire": "memoire",
    "mémoires": "memoire",
}


def _cle(texte: str) -> str:
    """Comparaison insensible à la casse, aux accents et aux espaces."""
    texte = unicodedata.normalize("NFKD", (texte or "").strip().lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(texte.split())


def _separer(reference: str):
    """« UCAD/Thèses » → ("UCAD", "Thèses") ; « Thèses » → (None, "Thèses")."""
    if "/" in reference:
        code, reste = reference.split("/", 1)
        return code.strip().upper(), reste.strip()
    return None, reference.strip()


class Regles:
    """Règles chargées une fois, prêtes à être appliquées en série."""

    def __init__(self, regles):
        self.document = {}
        self.sous_collection = {}   # (code ou None, nom normalisé) → accès
        self.collection = {}        # (code ou None, 'these'/'memoire') → accès
        self.etablissement = {}     # code → accès
        for r in regles:
            acces = r.acces if r.acces in (PUBLIC, RESTREINT) else None
            if acces is None:
                continue
            if r.niveau == "document":
                self.document[r.reference.strip().lower()] = acces
            elif r.niveau == "sous_collection":
                code, nom = _separer(r.reference)
                self.sous_collection[(code, _cle(nom))] = acces
            elif r.niveau == "collection":
                code, nom = _separer(r.reference)
                type_doc = COLLECTIONS.get(_cle(nom).replace(" ", ""))
                if type_doc:
                    self.collection[(code, type_doc)] = acces
            elif r.niveau == "etablissement":
                self.etablissement[r.reference.strip().upper()] = acces

    def acces(self, doc) -> str:
        code = (doc.etablissement_code or "").upper()

        valeur = self.document.get(str(doc.id).lower())
        if valeur:
            return valeur

        if doc.sous_entite_nom:
            nom = _cle(doc.sous_entite_nom)
            valeur = (self.sous_collection.get((code, nom))
                      or self.sous_collection.get((None, nom)))
            if valeur:
                return valeur

        valeur = (self.collection.get((code, doc.type))
                  or self.collection.get((None, doc.type)))
        if valeur:
            return valeur

        return self.etablissement.get(code, PUBLIC)


def charger(db: Session) -> Regles:
    return Regles(db.query(AccesException).all())


def recalculer(db: Session, requete=None) -> int:
    """Réécrit documents.acces d'après les règles. Renvoie le nombre de
    documents dont l'accès a changé. Ne valide pas la transaction."""
    regles = charger(db)
    changes = 0
    for doc in (requete if requete is not None else db.query(Document)).all():
        valeur = regles.acces(doc)
        if doc.acces != valeur:
            doc.acces = valeur
            changes += 1
    return changes


def url_publique(doc):
    """Lien vers le texte intégral, seulement si l'accès est public."""
    return doc.url_document if getattr(doc, "acces", PUBLIC) != RESTREINT else None
