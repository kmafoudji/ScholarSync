"""
Rapports PDF (WeasyPrint) : rapport d'activité et liste de documents.

Le bouton « Rapport PDF » renvoyait jusqu'ici une page HTML, et l'export
« PDF » de la liste répondait « Format non supporté ». Les deux
produisent désormais un vrai fichier PDF, à l'identité de l'outil
(couleurs, logo, police), limité au périmètre de l'utilisateur : un
établissement n'obtient que ses propres chiffres.

Le rendu se fait sans navigateur : les graphiques sont des barres HTML,
qui s'impriment nettement à toute échelle.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Document, Etablissement, SyncLog, ZoteroSource

RACINE_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # …/app


def chemin_local(url: str | None) -> str | None:
    """« /static/img/uploads/logo.png?v=123 » → chemin relatif lisible par
    WeasyPrint (base_url = dossier app/). None si le fichier n'existe pas :
    un logo manquant ne doit pas faire échouer le rapport."""
    if not url:
        return None
    chemin = urlparse(url).path.lstrip("/")
    if not chemin.startswith("static/") or ".." in chemin:
        return None
    return chemin if os.path.isfile(os.path.join(RACINE_APP, chemin)) else None


def _contexte_commun(params: dict, etablissement):
    return {
        "params": params,
        "etablissement": etablissement,
        "logo_outil": chemin_local(params.get("logo_url")),
        "logo_etab": chemin_local(getattr(etablissement, "logo_url", None)),
        "genere_le": datetime.now().strftime("%d/%m/%Y à %H:%M"),
    }


def _pdf(env, gabarit: str, contexte: dict) -> bytes:
    from weasyprint import HTML  # import tardif : chargement lourd
    html = env.get_template(gabarit).render(**contexte)
    return HTML(string=html, base_url=RACINE_APP).write_pdf()


def rapport_activite(env, db: Session, params: dict, stats: dict, code: str | None) -> bytes:
    def docs():
        q = db.query(Document)
        return q.filter(Document.etablissement_code == code) if code else q

    etablissement = (
        db.query(Etablissement).filter(Etablissement.code == code).first() if code else None
    )

    # Répartition : par établissement (national) ou par faculté (établissement)
    lignes = []
    if code:
        cle = Document.sous_entite_nom
        for nom, type_doc, n in (
            docs().with_entities(cle, Document.type, func.count())
            .group_by(cle, Document.type).all()
        ):
            nom = nom or "Non renseigné"
            ligne = next((l for l in lignes if l["nom"] == nom), None)
            if ligne is None:
                ligne = {"code": None, "nom": nom, "theses": 0, "memoires": 0}
                lignes.append(ligne)
            ligne["theses" if type_doc == "these" else "memoires"] += n
    else:
        noms = {e.code: e.nom for e in db.query(Etablissement).all()}
        for c, type_doc, n in (
            db.query(Document.etablissement_code, Document.type, func.count())
            .group_by(Document.etablissement_code, Document.type).all()
        ):
            ligne = next((l for l in lignes if l["code"] == c), None)
            if ligne is None:
                ligne = {"code": c, "nom": noms.get(c, ""), "theses": 0, "memoires": 0}
                lignes.append(ligne)
            ligne["theses" if type_doc == "these" else "memoires"] += n
    for l in lignes:
        l["total"] = l["theses"] + l["memoires"]
    lignes.sort(key=lambda l: -l["total"])

    annees = {}
    for annee, type_doc, n in (
        docs().with_entities(Document.annee, Document.type, func.count())
        .group_by(Document.annee, Document.type).all()
    ):
        a = annees.setdefault(annee, {"annee": annee, "theses": 0, "memoires": 0})
        a["theses" if type_doc == "these" else "memoires"] += n
    lignes_annees = sorted(annees.values(), key=lambda a: a["annee"] or 0, reverse=True)[:15]

    from app.services import domaines as domaines_reesao
    lignes_domaines = [
        {"domaine": domaines_reesao.libelle(d) if d else "Non classé", "count": n}
        for d, n in docs().with_entities(Document.domaine_reesao, func.count())
        .group_by(Document.domaine_reesao).order_by(func.count().desc()).all()
    ]

    logs_q = db.query(SyncLog)
    if code:
        logs_q = logs_q.filter(SyncLog.etablissement_code == code)
    logs = logs_q.order_by(SyncLog.debut.desc()).limit(8).all()

    depuis = datetime.now(timezone.utc) - timedelta(days=30)
    contexte = _contexte_commun(params, etablissement)
    contexte.update({
        "stats": stats,
        "nb_restreints": docs().filter(Document.acces == "restreint").count(),
        "nb_etablissements": len(lignes),
        "nb_ajouts_30j": docs().filter(Document.created_at >= depuis).count(),
        "lignes_repartition": lignes,
        "max_repartition": max((l["total"] for l in lignes), default=0),
        "lignes_annees": lignes_annees,
        "max_annee": max((a["theses"] + a["memoires"] for a in lignes_annees), default=0),
        "lignes_domaines": lignes_domaines,
        "max_domaine": max((d["count"] for d in lignes_domaines), default=0),
        "logs": logs,
    })
    return _pdf(env, "pdf/rapport.html", contexte)


def liste_documents(env, db: Session, params: dict, docs: list, code: str | None,
                    filtres: str = "") -> bytes:
    etablissement = (
        db.query(Etablissement).filter(Etablissement.code == code).first() if code else None
    )
    contexte = _contexte_commun(params, etablissement)
    contexte.update({"docs": docs, "filtres": filtres})
    return _pdf(env, "pdf/liste.html", contexte)
