"""
ScholarSync — Moteur de synchronisation Zotero
Moissonne les groupes Zotero configurés et indexe les documents
"""
from sqlalchemy.orm import Session
from datetime import datetime
from pyzotero import zotero
from app.models import ZoteroSource, Document, SyncLog, NumerotationCompteur
from app.services.numerotation import generer_numero
import logging

logger = logging.getLogger(__name__)

TAGS_STATUTS = {"statut: soutenu": "soutenu", "statut: en_preparation": "en_preparation"}
DOMAINES_CAMES = {
    "sciences naturelles et agronomie", "lettres et sciences humaines",
    "sciences et techniques de l'ingénieur", "sciences juridiques et politiques",
    "sciences économiques et gestion", "sciences de la santé",
    "sciences de l'éducation", "sciences et technologies de l'information",
}

def extraire_tags(item_tags: list) -> dict:
    """Extrait statut et domaine depuis les tags Zotero."""
    result = {"statut": None, "domaine": None}
    for tag_obj in item_tags:
        tag = tag_obj.get("tag", "").lower().strip()
        if tag in TAGS_STATUTS:
            result["statut"] = TAGS_STATUTS[tag]
        elif tag.startswith("domaine:"):
            domaine = tag.replace("domaine:", "").strip().title()
            result["domaine"] = domaine
        elif tag.lower() in DOMAINES_CAMES:
            result["domaine"] = tag.title()
    return result

def determiner_type_et_sous_entite(collection_path: list) -> dict:
    """
    Déduit le type et la sous-entité depuis le chemin de collections Zotero.
    Niveau 1 : Thèses | Mémoires → type
    Niveau 2 : École doctorale / Faculté → sous_entite_nom
    """
    result = {"type": None, "sous_entite_nom": None, "sous_entite_type": None}
    if not collection_path:
        return result
    niveau1 = collection_path[0].lower() if collection_path else ""
    if "thèse" in niveau1 or "these" in niveau1 or "thèses" in niveau1:
        result["type"] = "these"
        result["sous_entite_type"] = "ecole_doctorale"
    elif "mémoire" in niveau1 or "memoire" in niveau1:
        result["type"] = "memoire"
        result["sous_entite_type"] = "faculte"
    if len(collection_path) >= 2:
        result["sous_entite_nom"] = collection_path[1]
    return result

async def sync_source(db: Session, source: ZoteroSource):
    """Synchronise une source Zotero unique."""
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
        items = zot.everything(zot.items(since=since, itemType="thesis || book"))

        # Charger les collections une fois
        collections = {}
        try:
            for col in zot.everything(zot.collections()):
                collections[col["key"]] = col["data"]["name"]
        except:
            pass

        added = modified = errors = 0

        for item in items:
            try:
                data = item.get("data", {})
                if data.get("itemType") not in ("thesis", "book", "document"):
                    continue

                # Chemin de collection
                col_keys = data.get("collections", [])
                col_path = [collections.get(k, "") for k in col_keys if k in collections]

                type_info = determiner_type_et_sous_entite(col_path)
                if not type_info["type"]:
                    continue

                tag_info = extraire_tags(data.get("tags", []))
                statut = tag_info.get("statut") or "soutenu"

                # Année
                date_str = data.get("date", "")
                annee = None
                if date_str:
                    try:
                        annee = int(date_str[:4])
                    except:
                        pass
                annee = annee or datetime.now().year

                # Auteur
                creators = data.get("creators", [])
                auteur = ""
                for c in creators:
                    if c.get("creatorType") == "author":
                        auteur = f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
                        break
                if not auteur and creators:
                    c = creators[0]
                    auteur = f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()

                directeur = ""
                for c in creators:
                    if c.get("creatorType") in ("contributor", "editor"):
                        directeur = f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
                        break

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
                        mots_cles=[t["tag"] for t in data.get("tags", []) if not t["tag"].startswith("statut:") and not t["tag"].startswith("domaine:")],
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

        # Mettre à jour le cursor de version
        try:
            new_version = zot.last_modified_version()
            source.zotero_version = new_version
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
    """Lance la synchronisation de toutes les sources actives."""
    sources = db.query(ZoteroSource).filter(ZoteroSource.actif == True).all()
    for source in sources:
        await sync_source(db, source)
