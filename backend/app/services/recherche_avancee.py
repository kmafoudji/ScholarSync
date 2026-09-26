"""
Recherche avancée du catalogue public : plusieurs critères « champ +
texte », reliés par ET ou par OU.

    /recherche?champ=titre&terme=irrigation&champ=auteur&terme=diop&op=et

Les critères voyagent dans l'adresse, en deux listes appariées par leur
rang (champ[i] ↔ terme[i]) : une recherche avancée se partage, se met en
favori et s'exporte comme une recherche simple. Les facettes (type,
établissement, année…) s'y ajoutent toujours en ET.

Avec Meilisearch, chaque critère est une recherche limitée à ses champs
(attributesToSearchOn), tous les mots exigés ; les listes obtenues sont
croisées (ET) ou réunies (OU). Sans moteur, le repli SQL applique la même
règle : chaque mot du critère doit figurer dans l'un de ses champs.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.models import Document, Etablissement

MAX_CRITERES = 10
LONGUEUR_MAX = 200

# clé → (libellé, champs de l'index Meilisearch)
CHAMPS = {
    "tout":          ("Tous les champs", None),
    "titre":         ("Titre", ["titre"]),
    "auteur":        ("Auteur", ["auteur"]),
    "directeur":     ("Direction", ["directeur"]),
    "mots_cles":     ("Mots-clés", ["mots_cles"]),
    "resume":        ("Résumé", ["resume"]),
    "domaine":       ("Discipline", ["domaine"]),
    "sous_entite":   ("École doctorale / Faculté", ["sous_entite_nom"]),
    "etablissement": ("Établissement", ["etablissement_nom", "etablissement_code"]),
    "numero":        ("Numéro national", ["numero_national"]),
}

# Lignes proposées quand on ouvre la recherche avancée
CHAMPS_PAR_DEFAUT = ["titre", "auteur", "domaine"]


def lire(champs, termes, op: str = ""):
    """Critères propres à partir de l'adresse : [(champ, terme)], op.

    Les critères sans texte sont ignorés, un champ inconnu devient « Tous
    les champs », le nombre et la longueur sont bornés.
    """
    from app.services.numerotation import MOTIF_ANCIEN, normaliser
    criteres = []
    for i, terme in enumerate(termes or []):
        terme = " ".join((terme or "").split())[:LONGUEUR_MAX]
        if not terme:
            continue
        champ = champs[i] if i < len(champs or []) else "tout"
        champ = champ if champ in CHAMPS else "tout"
        # Un numéro noté à l'ancienne forme (SCUCTS…) retrouve son document
        if MOTIF_ANCIEN.match(terme.upper().replace(" ", "").replace("-", "")):
            terme = normaliser(terme)
        criteres.append((champ, terme))
        if len(criteres) == MAX_CRITERES:
            break
    return criteres, ("ou" if op == "ou" else "et")


def depuis_filtres(filtres: dict):
    return lire(filtres.get("champ") or [], filtres.get("terme") or [], filtres.get("op") or "")


def vers_filtres(criteres, op) -> dict:
    """Clés à ranger dans le dict des filtres (vide sans critère)."""
    if not criteres:
        return {}
    sortie = {"champ": [c for c, _ in criteres], "terme": [t for _, t in criteres]}
    if op == "ou":
        sortie["op"] = "ou"
    return sortie


def combiner(listes, op: str):
    """Croise (ET) ou réunit (OU) des listes d'identifiants ordonnées.

    L'ordre de pertinence suit la première liste ; en OU, les suivantes
    complètent dans leur propre ordre.
    """
    if not listes:
        return []
    if op == "ou":
        vus, sortie = set(), []
        for liste in listes:
            for i in liste:
                if i not in vus:
                    vus.add(i)
                    sortie.append(i)
        return sortie
    communs = set(listes[0])
    for liste in listes[1:]:
        communs &= set(liste)
    return [i for i in listes[0] if i in communs]


def ids_moteur(criteres, op, chercher):
    """Identifiants ordonnés, ou None si le moteur est indisponible."""
    listes = []
    for champ, terme in criteres:
        ids = chercher(terme, CHAMPS[champ][1], True)
        if ids is None:
            return None
        listes.append(ids)
    return combiner(listes, op)


# ── Repli SQL ──────────────────────────────────────────────────────

def _colonnes(champ):
    mots_cles = func.coalesce(func.array_to_string(Document.mots_cles, " "), "")
    par_champ = {
        "titre": [Document.titre],
        "auteur": [Document.auteur],
        "directeur": [Document.directeur],
        "mots_cles": [mots_cles],
        "resume": [Document.resume],
        "domaine": [Document.domaine],
        "sous_entite": [Document.sous_entite_nom],
        "numero": [Document.numero_national],
    }
    if champ == "etablissement":
        return None  # traité à part : le nom est dans une autre table
    if champ == "tout":
        return [Document.titre, Document.auteur, Document.directeur, mots_cles,
                Document.resume, Document.domaine, Document.sous_entite_nom,
                Document.numero_national]
    return par_champ[champ]


def _condition_mot(champ, mot):
    motif = f"%{mot}%"
    if champ == "etablissement" or champ == "tout":
        codes = select(Etablissement.code).where(
            Etablissement.nom.ilike(motif) | Etablissement.code.ilike(motif))
        cond_etab = Document.etablissement_code.in_(codes)
        if champ == "etablissement":
            return cond_etab
        return or_(cond_etab, *[c.ilike(motif) for c in _colonnes("tout")])
    return or_(*[c.ilike(motif) for c in _colonnes(champ)])


def condition_sql(criteres, op):
    """Expression SQL équivalente : chaque mot d'un critère doit figurer
    dans l'un de ses champs ; critères reliés par ET ou OU."""
    parties = [and_(*[_condition_mot(champ, mot) for mot in terme.split()])
               for champ, terme in criteres]
    return or_(*parties) if op == "ou" else and_(*parties)
