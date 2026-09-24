"""
Recherche plein texte avec Meilisearch, repli SQL si le moteur manque.

Jusqu'ici la recherche était un ILIKE '%mot%' sur quatre colonnes :
aucune tolérance aux fautes (« thèse » ≠ « these »), aucun classement
par pertinence, et une lecture complète de la table à chaque requête —
acceptable pour quelques milliers de notices, pas pour un catalogue
national.

Meilisearch ne sert qu'à répondre à « quels documents correspondent à
ce texte, dans quel ordre ». Les facettes, le cloisonnement, les règles
d'accès et la pagination restent calculés en SQL sur ces identifiants :
la base reste la seule source de vérité, et le moteur peut être vidé et
reconstruit à tout moment (reindexer_tout).

Si Meilisearch est absent ou injoignable, la recherche retombe sur le
SQL sans erreur visible ; un nouvel essai a lieu au bout d'une minute.
"""
from __future__ import annotations

import logging
import threading
import time

from app.core.config import settings

logger = logging.getLogger("scholarsync.recherche")

INDEX = "documents"
MAX_RESULTATS = 1000     # au-delà, un lecteur affine plutôt que de feuilleter
_REESSAI_S = 60

_etat = {"client": None, "hors_ligne_depuis": 0.0}
_verrou = threading.Lock()

CHAMPS_CHERCHABLES = [
    "titre", "auteur", "mots_cles", "numero_national", "directeur",
    "resume", "domaine", "sous_entite_nom", "etablissement_nom", "etablissement_code",
]


def _client():
    """Client prêt, ou None si le moteur n'est pas joignable."""
    if not settings.MEILISEARCH_URL:
        return None
    if _etat["client"] is not None:
        return _etat["client"]
    if time.time() - _etat["hors_ligne_depuis"] < _REESSAI_S:
        return None
    with _verrou:
        try:
            import meilisearch
            client = meilisearch.Client(settings.MEILISEARCH_URL,
                                        settings.MEILISEARCH_KEY or None, timeout=3)
            client.health()
            _configurer(client)
            _etat["client"] = client
            logger.info("Meilisearch disponible : %s", settings.MEILISEARCH_URL)
        except Exception as e:
            _etat["hors_ligne_depuis"] = time.time()
            logger.info("Meilisearch indisponible (%s) : recherche SQL", str(e)[:80])
            return None
    return _etat["client"]


def _perdre_client(e):
    logger.warning("Meilisearch ne répond plus (%s) : repli SQL", str(e)[:80])
    _etat["client"] = None
    _etat["hors_ligne_depuis"] = time.time()


def _configurer(client):
    client.create_index(INDEX, {"primaryKey": "id"})
    index = client.index(INDEX)
    index.update_settings({
        "searchableAttributes": CHAMPS_CHERCHABLES,
        # L'ordre des champs cherchables compte : un mot trouvé dans le
        # titre classe mieux qu'un mot trouvé dans le résumé.
        "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
        "typoTolerance": {"minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
                          # Un numéro national est un identifiant, pas un mot
                          "disableOnAttributes": ["numero_national", "etablissement_code"]},
        "pagination": {"maxTotalHits": MAX_RESULTATS},
    })


def disponible() -> bool:
    return _client() is not None


def _fiche(doc, noms_etabs: dict) -> dict:
    return {
        "id": str(doc.id),
        "titre": doc.titre,
        "auteur": doc.auteur,
        "mots_cles": list(doc.mots_cles or []),
        "numero_national": doc.numero_national,
        "directeur": doc.directeur,
        "resume": (doc.resume or "")[:5000],
        "domaine": doc.domaine,
        "sous_entite_nom": doc.sous_entite_nom,
        "etablissement_code": doc.etablissement_code,
        "etablissement_nom": noms_etabs.get(doc.etablissement_code),
    }


def _noms_etabs(db):
    from app.models import Etablissement
    return {e.code: e.nom for e in db.query(Etablissement).all()}


def indexer(db, documents) -> None:
    """Ajoute ou met à jour des documents dans le moteur (sans attendre)."""
    client = _client()
    documents = list(documents)
    if client is None or not documents:
        return
    noms = _noms_etabs(db)
    try:
        client.index(INDEX).add_documents([_fiche(d, noms) for d in documents])
    except Exception as e:
        _perdre_client(e)


def retirer(ids) -> None:
    client = _client()
    ids = [str(i) for i in ids]
    if client is None or not ids:
        return
    try:
        client.index(INDEX).delete_documents(ids)
    except Exception as e:
        _perdre_client(e)


def reindexer_tout(db) -> int:
    """Reconstruit l'index depuis la base. Renvoie le nombre de documents."""
    from app.models import Document
    client = _client()
    if client is None:
        return 0
    noms = _noms_etabs(db)
    try:
        index = client.index(INDEX)
        tache = index.delete_all_documents()
        client.wait_for_task(tache.task_uid, timeout_in_ms=60000)
        total, lot = 0, []
        for doc in db.query(Document).yield_per(500):
            lot.append(_fiche(doc, noms))
            if len(lot) >= 500:
                index.add_documents(lot)
                total += len(lot)
                lot = []
        if lot:
            index.add_documents(lot)
            total += len(lot)
        return total
    except Exception as e:
        _perdre_client(e)
        return 0


def verifier_au_demarrage(session_factory) -> None:
    """En tâche de fond : reconstruit l'index s'il ne correspond pas à la
    base (premier démarrage, moteur vidé, documents ajoutés sans lui)."""
    def travail():
        db = session_factory()
        try:
            from app.models import Document
            client = _client()
            if client is None:
                return
            attendu = db.query(Document).count()
            try:
                present = client.index(INDEX).get_stats().number_of_documents
            except Exception:
                present = -1
            if present != attendu:
                n = reindexer_tout(db)
                logger.info("Index de recherche reconstruit : %s document(s)", n)
        except Exception:
            logger.warning("Vérification de l'index impossible", exc_info=True)
        finally:
            db.close()
    threading.Thread(target=travail, name="index-recherche", daemon=True).start()


_cache: dict = {}
_CACHE_S = 30


def chercher(texte: str):
    """Identifiants des documents correspondant à `texte`, du plus au moins
    pertinent ; None si le moteur est indisponible (repli SQL).

    Une page de catalogue interroge le texte une fois par facette : le
    résultat est gardé quelques secondes pour ne pas multiplier les appels.
    """
    texte = (texte or "").strip()
    if not texte:
        return None
    maintenant = time.time()
    enregistre = _cache.get(texte)
    if enregistre and maintenant - enregistre[0] < _CACHE_S:
        return enregistre[1]
    client = _client()
    if client is None:
        return None
    try:
        reponse = client.index(INDEX).search(
            texte, {"limit": MAX_RESULTATS, "attributesToRetrieve": ["id"]})
        ids = [h["id"] for h in reponse.get("hits", [])]
    except Exception as e:
        _perdre_client(e)
        return None
    if len(_cache) > 500:
        _cache.clear()
    _cache[texte] = (maintenant, ids)
    return ids


def vider_cache() -> None:
    _cache.clear()
