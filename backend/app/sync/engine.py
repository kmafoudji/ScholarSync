from sqlalchemy.orm import Session
from datetime import datetime
from pyzotero import zotero
from app.models import ZoteroSource, Document, SyncLog, NumerotationCompteur
from app.services.numerotation import generer_numero
from app.services import acces as acces_docs
import logging

logger = logging.getLogger(__name__)

TAGS_STATUTS = {"statut: soutenu": "soutenu", "statut: en_preparation": "en_preparation"}

def extraire_tags(item_tags: list) -> dict:
    result = {"statut": None, "domaine": None}
    for tag_obj in item_tags:
        tag = tag_obj.get("tag", "").lower().strip()
        if tag in TAGS_STATUTS:
            result["statut"] = TAGS_STATUTS[tag]
        elif tag.startswith("domaine:"):
            result["domaine"] = tag.replace("domaine:", "").strip().title()
    return result

def construire_arbre_collections(collections: list) -> dict:
    """Retourne un dict key -> {name, parent} et un dict key -> children"""
    tree = {}
    for c in collections:
        key = c["key"]
        tree[key] = {
            "name": c["data"]["name"],
            "parent": c["data"].get("parentCollection") or None
        }
    return tree

def determiner_type_depuis_arbre(col_keys: list, tree: dict) -> dict:
    """
    Remonte l'arbre des collections pour trouver Thèses ou Mémoires
    au niveau racine, et récupère le nom de la sous-collection directe.
    """
    result = {"type": None, "sous_entite_nom": None, "sous_entite_type": None}

    for col_key in col_keys:
        if col_key not in tree:
            continue

        # Remonter jusqu'à la racine
        current_key = col_key
        path = []
        visited = set()
        while current_key and current_key not in visited:
            visited.add(current_key)
            node = tree.get(current_key)
            if not node:
                break
            path.append((current_key, node["name"]))
            current_key = node["parent"]

        # path[0] = collection directe de l'item
        # path[-1] = collection racine (Thèses ou Mémoires)
        if not path:
            continue

        root_name = path[-1][1].lower()
        if "thèse" in root_name or "these" in root_name:
            result["type"] = "these"
            result["sous_entite_type"] = "ecole_doctorale"
        elif "mémoire" in root_name or "memoire" in root_name:
            result["type"] = "memoire"
            result["sous_entite_type"] = "faculte"

        # Sous-entité = collection juste en dessous de la racine
        if len(path) >= 2:
            result["sous_entite_nom"] = path[-2][1]
        elif len(path) == 1 and result["type"]:
            result["sous_entite_nom"] = path[0][1]

        if result["type"]:
            break

    return result

PROGRESS_EVERY = 25  # commit de l'avancement tous les N items


def _extraire_annee(date_str) -> int:
    if date_str:
        try:
            return int(str(date_str)[:4])
        except (TypeError, ValueError):
            pass
    return datetime.now().year


def _extraire_personnes(creators: list) -> tuple:
    auteur = directeur = ""
    for c in creators:
        role = c.get("creatorType", "")
        nom = f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
        if role == "author" and not auteur:
            auteur = nom
        elif role in ("contributor", "editor", "seriesEditor") and not directeur:
            directeur = nom
    if not auteur and creators:
        c = creators[0]
        auteur = f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
    return auteur, directeur


def sync_source(db: Session, source: ZoteroSource, declenchement: str = "auto") -> SyncLog:
    """
    Synchronise une source Zotero. Fonction bloquante : à lancer via
    app.core.tasks.run_in_background, jamais directement dans une route.
    """
    log = SyncLog(
        zotero_source_id=source.id,
        declenchement=declenchement,
        statut="en_cours",
        debut=datetime.now(),
        documents_total=0,
        documents_traites=0,
    )
    db.add(log)
    db.commit()

    try:
        zot = zotero.Zotero(source.zotero_id, source.zotero_type, source.api_key)
        since = source.zotero_version or 0

        raw_cols = zot.everything(zot.collections())
        tree = construire_arbre_collections(raw_cols)

        # includeTrashed : un item mis à la corbeille Zotero n'apparaît
        # plus dans /items. Sans ce paramètre, il restait publié sur le
        # portail jusqu'à ce que la corbeille soit vidée.
        items = zot.everything(zot.items(since=since, includeTrashed=1))

        log.documents_total = len(items)
        db.commit()

        added = modified = errors = removed = 0
        etab_code = source.etablissement.code

        def _retirer(cle: str, raison: str) -> int:
            """Retire du portail le document issu de l'item `cle` de CETTE
            source. Le numéro national n'est pas réattribué : le compteur
            de l'établissement ne recule jamais."""
            doc = (
                db.query(Document)
                .filter(Document.zotero_source_id == source.id,
                        Document.zotero_item_key == cle)
                .first()
            )
            if doc is None:
                return 0
            logger.info("Document retiré (%s) : %s %s", raison, doc.numero_national,
                        (doc.titre or "")[:40])
            db.delete(doc)
            return 1

        for index, item in enumerate(items, start=1):
            try:
                data = item.get("data", {})
                if data.get("deleted"):
                    removed += _retirer(data.get("key", ""), "corbeille Zotero")
                    db.commit()
                    continue
                if data.get("itemType") not in (
                    "thesis", "book", "document", "journalArticle", "report"
                ):
                    continue

                col_keys = data.get("collections", [])
                type_info = determiner_type_depuis_arbre(col_keys, tree)
                if not type_info["type"]:
                    # Sorti des collections Thèses / Mémoires : s'il était
                    # publié, il ne doit plus l'être.
                    removed += _retirer(data.get("key", ""), "hors collections Thèses/Mémoires")
                    db.commit()
                    logger.info(
                        "Item ignoré (type non détecté) : %s",
                        data.get("title", "")[:40],
                    )
                    continue

                tag_info = extraire_tags(data.get("tags", []))
                statut = tag_info.get("statut") or "soutenu"
                annee = _extraire_annee(data.get("date", ""))
                auteur, directeur = _extraire_personnes(data.get("creators", []))

                zotero_key = data.get("key", "")
                existing = (
                    db.query(Document)
                    .filter(Document.zotero_item_key == zotero_key)
                    .first()
                )

                if existing:
                    existing.titre = data.get("title", existing.titre)
                    existing.auteur = auteur or existing.auteur
                    existing.statut = statut
                    existing.domaine = tag_info.get("domaine") or existing.domaine
                    existing.annee = annee
                    existing.directeur = directeur or existing.directeur
                    existing.resume = data.get("abstractNote") or existing.resume
                    existing.url_document = data.get("url") or existing.url_document
                    existing.sous_entite_nom = (
                        type_info["sous_entite_nom"] or existing.sous_entite_nom
                    )
                    # Travail soutenu depuis la dernière synchronisation :
                    # il reçoit maintenant son numéro national. Un numéro
                    # déjà attribué n'est jamais retiré ni changé.
                    if statut == "soutenu" and not existing.numero_national:
                        existing.numero_national = generer_numero(
                            db, etablissement_code=etab_code, type_doc=existing.type,
                            statut=statut, annee=annee,
                        )
                    existing.synced_at = datetime.now()
                    modified += 1
                else:
                    numero = generer_numero(
                        db,
                        etablissement_code=etab_code,
                        type_doc=type_info["type"],
                        statut=statut,
                        annee=annee,
                    )
                    db.add(Document(
                        numero_national=numero,
                        titre=data.get("title", "Sans titre"),
                        auteur=auteur or "Auteur inconnu",
                        type=type_info["type"],
                        statut=statut,
                        langue=data.get("language") or "français",
                        annee=annee,
                        domaine=tag_info.get("domaine"),
                        etablissement_code=etab_code,
                        sous_entite_nom=type_info["sous_entite_nom"],
                        sous_entite_type=type_info["sous_entite_type"],
                        directeur=directeur,
                        resume=data.get("abstractNote"),
                        mots_cles=[
                            t["tag"] for t in data.get("tags", [])
                            if not t["tag"].lower().startswith(("statut:", "domaine:"))
                        ],
                        url_document=data.get("url"),
                        zotero_source_id=source.id,
                        zotero_item_key=zotero_key,
                        zotero_version=data.get("version"),
                        synced_at=datetime.now(),
                    ))
                    added += 1

            except Exception as e:
                # Un item fautif ne doit pas emporter toute la sync : on
                # annule sa transaction et on passe au suivant.
                logger.error("Erreur item %s : %s", item.get("key"), e)
                db.rollback()
                errors += 1

            if index % PROGRESS_EVERY == 0:
                log.documents_traites = index
                log.documents_ajoutes = added
                log.documents_modifies = modified
                log.documents_erreur = errors
                db.commit()

        # Items supprimés définitivement (corbeille vidée) depuis la
        # dernière version. Un échec ici compte comme une erreur : avancer
        # la version ferait perdre ces suppressions pour toujours.
        try:
            supprimes = (zot.deleted(since=since) or {}).get("items") or []
            for cle in supprimes:
                removed += _retirer(cle, "supprimé dans Zotero")
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Suppressions Zotero non récupérées pour la source %s", source.id)
            errors += 1

        # Règles d'accès appliquées aux documents nouveaux ou modifiés
        acces_docs.recalculer(
            db, db.query(Document).filter(Document.zotero_source_id == source.id)
        )
        db.commit()

        # La sync est incrémentale (items modifiés depuis zotero_version) :
        # avancer la version malgré des notices en erreur les ferait
        # disparaître des syncs suivantes, sans que personne ne le voie.
        # On ne l'avance donc que si tout est passé ; sinon la prochaine
        # sync reprend les mêmes items (les réussis sont mis à jour, pas
        # dupliqués).
        if errors == 0:
            try:
                source.zotero_version = zot.last_modified_version()
            except Exception:
                logger.warning("Version Zotero non récupérée pour la source %s", source.id)
        else:
            log.message_erreur = (
                f"{errors} notice(s) en erreur — elles seront retentées à la "
                "prochaine synchronisation. Détail dans les journaux du serveur."
            )

        source.derniere_sync = datetime.now()
        log.statut = "succes"
        log.documents_ajoutes = added
        log.documents_modifies = modified
        log.documents_erreur = errors
        log.documents_supprimes = removed
        log.documents_traites = len(items)
        log.fin = datetime.now()
        db.commit()
        logger.info(
            "Sync %s : +%s modifiés:%s retirés:%s erreurs:%s",
            etab_code, added, modified, removed, errors,
        )

    except Exception as e:
        logger.exception("Sync échouée pour la source %s", source.id)
        db.rollback()
        # Le rollback a pu détacher `log` : on le recharge avant d'écrire.
        log = db.query(SyncLog).filter(SyncLog.id == log.id).first()
        if log:
            log.statut = "erreur"
            log.message_erreur = str(e)[:500]
            log.fin = datetime.now()
            db.commit()

    return log


def sync_all(db: Session, declenchement: str = "auto") -> None:
    sources = db.query(ZoteroSource).filter(ZoteroSource.actif == True).all()  # noqa: E712
    logger.info("Sync globale : %s source(s) active(s)", len(sources))
    for source in sources:
        sync_source(db, source, declenchement=declenchement)
