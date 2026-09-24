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

    def origine(self, doc):
        """Règle qui décide de l'accès du document : (niveau, référence,
        accès), ou None si aucune règle ne s'applique (public par défaut).
        Sert à expliquer à l'administrateur POURQUOI un document est
        restreint — sans quoi un clic sur « Rendre public » semble ne
        rien faire quand une règle d'établissement le recouvre."""
        code = (doc.etablissement_code or "").upper()

        valeur = self.document.get(str(doc.id).lower())
        if valeur:
            return ("document", str(doc.id), valeur)

        if doc.sous_entite_nom:
            nom = _cle(doc.sous_entite_nom)
            for cle in ((code, nom), (None, nom)):
                if cle in self.sous_collection:
                    ref = f"{code}/{doc.sous_entite_nom}" if cle[0] else doc.sous_entite_nom
                    return ("sous_collection", ref, self.sous_collection[cle])

        for cle in ((code, doc.type), (None, doc.type)):
            if cle in self.collection:
                nom = "Thèses" if doc.type == "these" else "Mémoires"
                return ("collection", f"{code}/{nom}" if cle[0] else nom, self.collection[cle])

        if code in self.etablissement:
            return ("etablissement", code, self.etablissement[code])
        return None

    def acces(self, doc) -> str:
        o = self.origine(doc)
        return o[2] if o else PUBLIC


LIBELLES_NIVEAUX = {
    "etablissement": "Établissement",
    "collection": "Collection",
    "sous_collection": "Sous-collection",
    "document": "Document",
}


def dans_perimetre(regle, code: str, ids_documents: set) -> bool:
    """La règle concerne-t-elle uniquement l'établissement `code` ?

    C'est la condition pour qu'un administrateur d'établissement puisse
    la modifier ou la supprimer. Une règle nationale (« Thèses » sans
    préfixe) le concerne mais ne lui appartient pas.
    """
    ref = (regle.reference or "").strip()
    if regle.niveau == "etablissement":
        return ref.upper() == code
    if regle.niveau in ("collection", "sous_collection"):
        prefixe, _ = _separer(ref)
        return prefixe == code
    if regle.niveau == "document":
        return ref.lower() in ids_documents
    return False


def reference_pour_etablissement(niveau: str, reference: str, code: str):
    """Normalise la référence saisie par un administrateur d'établissement :
    elle est ramenée de force à son propre établissement. Renvoie None si
    la référence est inutilisable."""
    _, nom = _separer(reference or "")
    if niveau == "etablissement":
        return code
    if niveau == "collection":
        type_doc = COLLECTIONS.get(_cle(nom).replace(" ", ""))
        if not type_doc:
            return None
        return f"{code}/{'Thèses' if type_doc == 'these' else 'Mémoires'}"
    if niveau == "sous_collection":
        return f"{code}/{nom}" if nom else None
    return None


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
