"""
Moisson OAI-PMH : synchroniser un catalogue qui n'est pas Zotero.

OAI-PMH est le protocole standard d'exposition des catalogues : Koha,
PMB, DSpace, HAL, Omeka et la plupart des archives ouvertes le parlent.
Un établissement donne l'adresse de son entrepôt ; ScholarSync y lit,
au format Dublin Core (oai_dc), les notices ajoutées, modifiées ou
supprimées depuis la moisson précédente — le même principe que la
synchronisation Zotero, avec le même journal.

Un catalogue de bibliothèque contient aussi des livres, des revues… Ne
sont retenues que les notices reconnues comme thèses ou mémoires
(dc:type), ou toutes celles d'un ensemble (set) choisi, avec un type par
défaut. Les autres sont comptées comme ignorées.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import Document, SourceOAI, SyncLog
from app.services import acces as acces_docs
from app.services import notices
from app.services import recherche as moteur_recherche

logger = logging.getLogger(__name__)

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}
PAGES_MAX = 2000           # garde-fou contre un jeton de reprise qui boucle
DELAI_S = 30
TAILLE_REPONSE_MAX = 30 * 1024 * 1024


class ErreurOAI(Exception):
    """Entrepôt injoignable ou réponse inexploitable ; message pour l'utilisateur."""


# ── Protocole ────────────────────────────────────────────────────────

def valider_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I):
        raise ErreurOAI("Adresse invalide : elle doit commencer par http:// ou https://.")
    return url.split("?")[0]


def _requete(client: httpx.Client, url: str, params: dict) -> ET.Element:
    try:
        r = client.get(url, params=params, timeout=DELAI_S, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ErreurOAI(f"Entrepôt injoignable : {str(e)[:120]}")
    if r.status_code != 200:
        raise ErreurOAI(f"L'entrepôt répond {r.status_code}.")
    if len(r.content) > TAILLE_REPONSE_MAX:
        raise ErreurOAI("Réponse trop volumineuse.")
    contenu = r.content
    # Pas de DOCTYPE / entités : protection contre l'expansion d'entités
    if b"<!DOCTYPE" in contenu[:2000].upper() or b"<!ENTITY" in contenu.upper():
        raise ErreurOAI("Réponse refusée : elle contient une déclaration DOCTYPE.")
    try:
        racine = ET.fromstring(contenu)
    except ET.ParseError:
        raise ErreurOAI("La réponse n'est pas du XML OAI-PMH : vérifiez l'adresse de base.")
    if racine.tag != f"{{{NS['oai']}}}OAI-PMH":
        raise ErreurOAI("La réponse n'est pas du XML OAI-PMH : vérifiez l'adresse de base.")
    erreur = racine.find("oai:error", NS)
    if erreur is not None and erreur.get("code") != "noRecordsMatch":
        raise ErreurOAI(f"Erreur OAI « {erreur.get('code')} » : {(erreur.text or '').strip()[:150]}")
    return racine


def identifier(url: str, transport=None) -> dict:
    """Identify + ListSets : nom de l'entrepôt, granularité, ensembles."""
    url = valider_url(url)
    with httpx.Client(transport=transport) as client:
        racine = _requete(client, url, {"verb": "Identify"})
        ident = racine.find("oai:Identify", NS)
        if ident is None:
            raise ErreurOAI("L'entrepôt ne répond pas à Identify.")
        info = {
            "nom": (ident.findtext("oai:repositoryName", "", NS) or "").strip(),
            "granularite": (ident.findtext("oai:granularity", "YYYY-MM-DD", NS) or "").strip(),
            "suppressions": (ident.findtext("oai:deletedRecord", "no", NS) or "").strip(),
            "ensembles": [],
        }
        try:
            sets = _requete(client, url, {"verb": "ListSets"})
            for s in sets.iter(f"{{{NS['oai']}}}set"):
                info["ensembles"].append({
                    "spec": s.findtext("oai:setSpec", "", NS),
                    "nom": s.findtext("oai:setName", "", NS),
                })
                if len(info["ensembles"]) >= 300:
                    break
        except ErreurOAI:
            pass  # ensembles facultatifs dans le protocole
        return info


def _enregistrements(client, url: str, ensemble: str | None, depuis: str | None):
    """Parcourt ListRecords, pages comprises. Rend (en-tête, métadonnées)."""
    params = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    if ensemble:
        params["set"] = ensemble
    if depuis:
        params["from"] = depuis
    for _ in range(PAGES_MAX):
        racine = _requete(client, url, params)
        liste = racine.find("oai:ListRecords", NS)
        if liste is None:
            return
        for rec in liste.findall("oai:record", NS):
            yield rec.find("oai:header", NS), rec.find("oai:metadata/oai_dc:dc", NS)
        jeton = (liste.findtext("oai:resumptionToken", "", NS) or "").strip()
        if not jeton:
            return
        params = {"verb": "ListRecords", "resumptionToken": jeton}
    raise ErreurOAI("Trop de pages : la moisson a été interrompue.")


# ── Correspondance Dublin Core → notice ──────────────────────────────

def _textes(dc, champ: str) -> list[str]:
    return [" ".join((e.text or "").split()) for e in dc.findall(f"dc:{champ}", NS)
            if (e.text or "").strip()]


def type_depuis_dc(types: list[str]) -> str:
    texte = " ".join(types).lower()
    if not texte:
        return ""
    if re.search(r"master|m[ée]moire|mastersthesis|dipl[oô]me", texte):
        return "memoire"
    if re.search(r"th[èe]se|thesis|doctora|phd|dissertation", texte):
        return "these"
    return ""


def notice_depuis_dc(dc, type_defaut: str = "") -> tuple[str, dict] | None:
    """(type, champs) ou None si ce n'est pas une thèse ou un mémoire."""
    type_doc = type_depuis_dc(_textes(dc, "type")) or type_defaut
    titres = _textes(dc, "title")
    auteurs = _textes(dc, "creator")
    if type_doc not in ("these", "memoire") or not titres or not auteurs:
        return None
    annee = None
    for d in _textes(dc, "date"):
        m = re.search(r"(19|20)\d{2}", d)
        if m:
            annee = int(m.group(0))
            break
    if annee is None:
        return None
    descriptions = _textes(dc, "description")
    liens = [i for i in _textes(dc, "identifier") if re.match(r"^https?://", i, re.I)]
    sujets = _textes(dc, "subject")
    texte_statut = " ".join(descriptions + _textes(dc, "type")).lower()
    return type_doc, {
        "titre": titres[0][:1000],
        "auteur": "; ".join(auteurs)[:500],
        "statut": "en_preparation" if "en préparation" in texte_statut or "en preparation" in texte_statut else "soutenu",
        "annee": annee,
        "directeur": "; ".join(_textes(dc, "contributor"))[:500] or None,
        "domaine": None,
        "sous_entite_nom": None,
        "langue": (_textes(dc, "language") or [None])[0],
        "resume": max(descriptions, key=len)[:20000] if descriptions else None,
        "mots_cles": [s[:200] for s in sujets][:30],
        "url_document": liens[0] if liens else None,
    }


def cle_oai(identifiant: str) -> str:
    return "oai-" + hashlib.sha1(identifiant.encode()).hexdigest()[:16]


# ── Moisson ──────────────────────────────────────────────────────────

def moissonner(db: Session, source: SourceOAI, declenchement: str = "auto",
               transport=None) -> SyncLog:
    code = source.etablissement.code
    log = SyncLog(source_oai_id=source.id, etablissement_code=code,
                  declenchement=declenchement, statut="en_cours", debut=datetime.now(),
                  documents_total=0, documents_traites=0)
    db.add(log)
    db.commit()

    ajoutes = modifies = retires = ignores = erreurs = traites = 0
    cles_touchees, ids_retires = [], []
    debut_moisson = datetime.now(timezone.utc)
    try:
        url = valider_url(source.url)
        with httpx.Client(transport=transport) as client:
            for entete, dc in _enregistrements(client, url, source.ensemble, source.depuis):
                traites += 1
                if entete is None:
                    continue
                identifiant = (entete.findtext("oai:identifier", "", NS) or "").strip()
                if not identifiant:
                    continue
                cle = cle_oai(identifiant)
                # Point de sauvegarde par notice : une notice fautive est
                # annulée seule, sans emporter les précédentes.
                point = db.begin_nested()
                try:
                    if entete.get("status") == "deleted":
                        doc = db.query(Document).filter(Document.etablissement_code == code,
                                                        Document.zotero_item_key == cle).first()
                        if doc is not None:
                            ids_retires.append(notices.retirer(db, doc, "supprimé de l'entrepôt OAI"))
                            retires += 1
                        point.commit()
                        continue
                    lue = notice_depuis_dc(dc, source.type_defaut or "") if dc is not None else None
                    if lue is None:
                        # Plus une thèse (type changé) : retirée si elle était publiée
                        doc = db.query(Document).filter(Document.etablissement_code == code,
                                                        Document.zotero_item_key == cle).first()
                        if doc is not None:
                            ids_retires.append(notices.retirer(db, doc, "n'est plus une thèse ou un mémoire dans l'entrepôt"))
                            retires += 1
                        else:
                            ignores += 1
                        point.commit()
                        continue
                    type_doc, champs = lue
                    if notices.enregistrer(db, code, cle, type_doc, champs) == "ajout":
                        ajoutes += 1
                    else:
                        modifies += 1
                    cles_touchees.append(cle)
                    point.commit()
                except Exception:
                    logger.exception("Notice OAI %s", identifiant)
                    point.rollback()
                    erreurs += 1
                    continue
                if traites % 100 == 0:
                    log.documents_traites, log.documents_ajoutes = traites, ajoutes
                    log.documents_modifies, log.documents_erreur = modifies, erreurs
                    db.commit()

        acces_docs.recalculer(db, db.query(Document).filter(Document.etablissement_code == code))
        from app.services import domaines as domaines_reesao
        domaines_reesao.recalculer(db, db.query(Document).filter(Document.etablissement_code == code))
        # Comme pour Zotero : la date de reprise n'avance que si tout est passé
        if erreurs == 0:
            source.depuis = debut_moisson.strftime("%Y-%m-%d")
        else:
            log.message_erreur = (f"{erreurs} notice(s) en erreur — elles seront reprises "
                                  "à la prochaine synchronisation.")
        if ignores:
            note = f"{ignores} notice(s) ignorée(s) : ni thèse ni mémoire (type, titre, auteur ou année manquant)."
            log.message_erreur = f"{log.message_erreur} {note}".strip() if log.message_erreur else note
        source.derniere_sync = datetime.now()
        log.statut = "succes"
        log.documents_total = log.documents_traites = traites
        log.documents_ajoutes, log.documents_modifies = ajoutes, modifies
        log.documents_supprimes, log.documents_erreur = retires, erreurs
        log.fin = datetime.now()
        db.commit()

        if cles_touchees:
            moteur_recherche.indexer(db, db.query(Document).filter(
                Document.etablissement_code == code, Document.zotero_item_key.in_(cles_touchees)))
        moteur_recherche.retirer(ids_retires)
        moteur_recherche.vider_cache()
        logger.info("Moisson OAI %s : +%s ~%s -%s ignorées:%s erreurs:%s",
                    code, ajoutes, modifies, retires, ignores, erreurs)
    except Exception as e:
        logger.exception("Moisson OAI échouée pour %s", code)
        db.rollback()
        log = db.query(SyncLog).filter(SyncLog.id == log.id).first()
        if log:
            log.statut = "erreur"
            log.message_erreur = str(e)[:500]
            log.fin = datetime.now()
            db.commit()
    return log
