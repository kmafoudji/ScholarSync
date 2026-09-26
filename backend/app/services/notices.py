"""
Écriture d'une notice venue d'ailleurs que Zotero (import de fichier,
moisson OAI-PMH), et retrait d'une notice.

Les deux voies partagent les mêmes règles que la synchronisation Zotero :
- une notice est identifiée par une clé stable dans son établissement
  (« imp-… » pour un fichier, « oai-… » pour un entrepôt) ;
- le numéro national est attribué à la soutenance, jamais changé ensuite ;
- une notice retirée qui revient reprend son identifiant (son adresse)
  et son numéro ;
- un retrait laisse une trace dans documents_retires.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, DocumentRetire
from app.services.numerotation import generer_numero

CHAMPS = ("titre", "auteur", "statut", "annee", "directeur", "domaine",
          "sous_entite_nom", "langue", "resume", "mots_cles", "url_document")


def enregistrer(db: Session, code: str, cle: str, type_doc: str, champs: dict) -> str:
    """Crée ou met à jour la notice `cle` de l'établissement `code`.
    Renvoie « ajout » ou « maj ». Ne valide pas la transaction."""
    valeurs = {k: champs.get(k) for k in CHAMPS}
    valeurs["langue"] = valeurs.get("langue") or "français"
    valeurs["mots_cles"] = valeurs.get("mots_cles") or None
    valeurs["synced_at"] = datetime.now()

    # Domaine REESAO fourni par la source (colonne d'import) : il est
    # imposé ; absent, on ne touche pas au choix fait dans l'administration.
    if champs.get("domaine_manuel"):
        valeurs["domaine_manuel"] = champs["domaine_manuel"]

    doc = db.query(Document).filter(Document.etablissement_code == code,
                                    Document.zotero_item_key == cle).first()
    if doc is not None:
        for k, v in valeurs.items():
            setattr(doc, k, v)
        if valeurs["statut"] == "soutenu" and not doc.numero_national:
            doc.numero_national = generer_numero(db, etablissement_code=code, type_doc=doc.type,
                                                 statut="soutenu", annee=valeurs["annee"])
        return "maj"

    ancien = (db.query(DocumentRetire)
              .filter(DocumentRetire.etablissement_code == code,
                      DocumentRetire.zotero_item_key == cle)
              .order_by(DocumentRetire.retire_le.desc()).first())
    numero = ancien.numero_national if ancien and ancien.numero_national else None
    if numero is None:
        numero = generer_numero(db, etablissement_code=code, type_doc=type_doc,
                                statut=valeurs["statut"], annee=valeurs["annee"])
    if ancien is not None:
        db.query(DocumentRetire).filter(
            DocumentRetire.etablissement_code == code,
            DocumentRetire.zotero_item_key == cle,
        ).delete(synchronize_session=False)
    db.add(Document(
        id=ancien.document_id if ancien else uuid.uuid4(),
        type=type_doc, etablissement_code=code, zotero_item_key=cle,
        numero_national=numero, **valeurs,
    ))
    return "ajout"


def retirer(db: Session, doc: Document, raison: str) -> uuid.UUID:
    """Retire un document du portail en gardant sa trace. Renvoie son id."""
    db.add(DocumentRetire(
        document_id=doc.id, numero_national=doc.numero_national, titre=doc.titre,
        auteur=doc.auteur, type=doc.type, annee=doc.annee,
        etablissement_code=doc.etablissement_code, zotero_source_id=doc.zotero_source_id,
        zotero_item_key=doc.zotero_item_key, raison=raison,
    ))
    identifiant = doc.id
    db.delete(doc)
    return identifiant
