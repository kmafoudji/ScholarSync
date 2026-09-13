from sqlalchemy.orm import Session
from datetime import datetime
from pyzotero import zotero
from app.models import ZoteroSource, Document, SyncLog, NumerotationCompteur
from app.services.numerotation import generer_numero
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

async def sync_source(db: Session, source: ZoteroSource):
    log = SyncLog(
        zotero_source_id=source.id,
        declenchement="auto",
        statut="en_cours",
        debut=datetime.now()
    )
    db.add(log)
    db.commit()

    try:
        zot = zotero.Zotero(source.zotero_id, source.zotero_type, source.api_key)
        since = source.zotero_version or 0

        # Charger toutes les collections
        raw_cols = zot.everything(zot.collections())
        tree = construire_arbre_collections(raw_cols)

        # Charger tous les items
        items = zot.everything(zot.items(since=since))

        added = modified = errors = 0

        for item in items:
            try:
                data = item.get("data", {})
                if data.get("itemType") not in ("thesis", "book", "document", "journalArticle", "report"):
                    continue

                col_keys = data.get("collections", [])
                type_info = determiner_type_depuis_arbre(col_keys, tree)

                if not type_info["type"]:
                    logger.info(f"Item ignoré (pas de type détecté): {data.get('title','')[:40]}")
                    continue

                tag_info = extraire_tags(data.get("tags", []))
                statut = tag_info.get("statut") or "soutenu"

                # Année
                date_str = data.get("date", "")
                annee = None
                if date_str:
                    try:
                        annee = int(str(date_str)[:4])
                    except:
                        pass
                annee = annee or datetime.now().year

                # Auteur
                creators = data.get("creators", [])
                auteur = ""
                directeur = ""
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

                zotero_key = data.get("key", "")
                existing = db.query(Document).filter(Document.zotero_item_key == zotero_key).first()

                if existing:
                    existing.titre = data.get("title", existing.titre)
                    existing.auteur = auteur or existing.auteur
                    existing.statut = statut
                    existing.domaine = tag_info.get("domaine") or existing.domaine
                    existing.annee = annee
                    existing.directeur = directeur or existing.directeur
                    existing.resume = data.get("abstractNote") or existing.resume
                    existing.url_document = data.get("url") or existing.url_document
                    existing.sous_entite_nom = type_info["sous_entite_nom"] or existing.sous_entite_nom
                    existing.synced_at = datetime.now()
                    modified += 1
                else:
                    numero = generer_numero(
                        db,
                        etablissement_code=source.etablissement.code,
                        type_doc=type_info["type"],
                        statut=statut,
                        annee=annee
                    )
                    doc = Document(
                        numero_national=numero,
                        titre=data.get("title", "Sans titre"),
                        auteur=auteur or "Auteur inconnu",
                        type=type_info["type"],
                        statut=statut,
                        langue=data.get("language") or "français",
                        annee=annee,
                        domaine=tag_info.get("domaine"),
                        etablissement_code=source.etablissement.code,
                        sous_entite_nom=type_info["sous_entite_nom"],
                        sous_entite_type=type_info["sous_entite_type"],
                        directeur=directeur,
                        resume=data.get("abstractNote"),
                        mots_cles=[t["tag"] for t in data.get("tags", [])
                                   if not t["tag"].lower().startswith(("statut:", "domaine:"))],
                        url_document=data.get("url"),
                        zotero_source_id=source.id,
                        zotero_item_key=zotero_key,
                        zotero_version=data.get("version"),
                        synced_at=datetime.now()
                    )
                    db.add(doc)
                    added += 1

            except Exception as e:
                logger.error(f"Erreur item {item.get('key')}: {e}")
                errors += 1

        try:
            source.zotero_version = zot.last_modified_version()
        except:
            pass

        source.derniere_sync = datetime.now()
        log.statut = "succes"
        log.documents_ajoutes = added
        log.documents_modifies = modified
        log.documents_erreur = errors
        log.fin = datetime.now()
        db.commit()
        logger.info(f"Sync {source.etablissement.code}: +{added} modifiés:{modified} erreurs:{errors}")

    except Exception as e:
        log.statut = "erreur"
        log.message_erreur = str(e)[:500]
        log.fin = datetime.now()
        db.commit()
        logger.error(f"Sync échouée pour source {source.id}: {e}")

async def sync_all(db: Session):
    sources = db.query(ZoteroSource).filter(ZoteroSource.actif == True).all()
    for source in sources:
        await sync_source(db, source)
