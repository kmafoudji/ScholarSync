"""
Données de démonstration : installer et retirer en une commande.

    docker exec scholarsync-app python -m app.outils.demo installer
    docker exec scholarsync-app python -m app.outils.demo retirer
    docker exec scholarsync-app python -m app.outils.demo etat

Crée, si elles manquent, trois universités (UCAD, UGB, UADB) et une
soixantaine de notices fictives réparties entre facultés, écoles
doctorales, domaines et années, dont une part en préparation. Chaque
notice porte la mention « Notice de démonstration » dans son résumé, et
une clé « demo-… » qui permet de toutes les retirer sans toucher aux
notices réelles.

À utiliser de préférence sur une instance de démonstration. Sur
l'instance de production, lancez « retirer » après la présentation : les
numéros nationaux attribués aux notices de démonstration ne seront pas
réattribués (ils sont consignés dans les documents retirés).
"""
from __future__ import annotations

import hashlib
import random
import sys

from app.core.database import SessionLocal
from app.models import Document, DocumentRetire, Etablissement
from app.services import acces as acces_docs
from app.services import recherche
from app.services.numerotation import generer_numero

PREFIXE = "demo-"
MENTION = "Notice de démonstration — ScholarSync."

ETABLISSEMENTS = [
    ("UCAD", "Université Cheikh Anta Diop de Dakar", "UCAD", "Dakar", "UC"),
    ("UGB", "Université Gaston Berger de Saint-Louis", "UGB", "Saint-Louis", "UG"),
    ("UADB", "Université Alioune Diop de Bambey", "UADB", "Bambey", "UB"),
]

FACULTES = {
    "UCAD": [("École doctorale ETHOS", "ecole_doctorale"), ("École doctorale SEV", "ecole_doctorale"),
             ("FASEG", "faculte"), ("FLSH", "faculte"), ("Faculté des Sciences et Techniques", "faculte")],
    "UGB": [("UFR SAT", "faculte"), ("UFR SJP", "faculte"), ("École doctorale SHS", "ecole_doctorale")],
    "UADB": [("UFR S2ATA", "faculte"), ("UFR SATIC", "faculte")],
}

SUJETS = [
    ("Économie", "L'impact du microcrédit sur l'entrepreneuriat féminin en milieu rural",
     ["microfinance", "entrepreneuriat", "genre"]),
    ("Économie", "Transferts de fonds des migrants et consommation des ménages",
     ["migrations", "ménages", "transferts"]),
    ("Droit", "La décentralisation et la gouvernance des collectivités territoriales",
     ["décentralisation", "collectivités", "gouvernance"]),
    ("Droit", "Le régime juridique du foncier rural : entre coutume et droit positif",
     ["foncier", "droit coutumier"]),
    ("Sociologie", "Jeunesse urbaine et engagement associatif dans les quartiers périphériques",
     ["jeunesse", "associations", "ville"]),
    ("Histoire", "Les archives coloniales et l'écriture de l'histoire locale",
     ["archives", "histoire coloniale"]),
    ("Lettres", "Oralité et écriture dans le roman africain contemporain",
     ["littérature", "oralité"]),
    ("Géographie", "Dynamiques du littoral et érosion côtière",
     ["littoral", "érosion", "climat"]),
    ("Agronomie", "Amélioration de la productivité du mil par la gestion de la fertilité des sols",
     ["mil", "sols", "fertilité"]),
    ("Agronomie", "Irrigation goutte-à-goutte et maraîchage dans la vallée du fleuve",
     ["irrigation", "maraîchage"]),
    ("Informatique", "Détection automatique des maladies des cultures par apprentissage profond",
     ["apprentissage profond", "agriculture", "vision"]),
    ("Informatique", "Paiement mobile et inclusion financière : analyse des usages",
     ["paiement mobile", "inclusion financière"]),
    ("Santé publique", "Couverture vaccinale et facteurs d'abandon chez l'enfant",
     ["vaccination", "santé infantile"]),
    ("Environnement", "Gestion des déchets plastiques en milieu urbain",
     ["déchets", "plastique", "ville"]),
    ("Mathématiques", "Modélisation stochastique de la propagation des épidémies",
     ["modélisation", "épidémiologie"]),
    ("Physique", "Caractérisation de matériaux pour cellules photovoltaïques",
     ["photovoltaïque", "matériaux"]),
]

PRENOMS = ["Aminata", "Mamadou", "Fatou", "Cheikh", "Awa", "Ousmane", "Mariama", "Ibrahima",
           "Khady", "Abdoulaye", "Ndèye", "Moussa", "Aïssatou", "Pape", "Rokhaya", "Serigne"]
NOMS = ["NDIAYE", "DIOP", "FALL", "SOW", "BA", "SY", "GUEYE", "DIALLO", "FAYE", "CISSÉ",
        "MBAYE", "SARR", "KANE", "NIANG", "THIAM", "DIENG"]


def _personne(r):
    return f"{r.choice(NOMS)} {r.choice(PRENOMS)}"


def installer():
    db = SessionLocal()
    r = random.Random(2026)  # mêmes notices à chaque installation
    try:
        for code, nom, court, ville, code_numero in ETABLISSEMENTS:
            etab = db.query(Etablissement).filter(Etablissement.code == code).first()
            if not etab:
                db.add(Etablissement(code=code, nom=nom, nom_court=court, ville=ville,
                                     pays="Sénégal", code_numero=code_numero))
            elif not etab.actif:
                # Inscrit par le référentiel mais pas encore activé : la
                # démonstration a besoin de le montrer sur le portail.
                etab.actif = True
        db.commit()

        crees = 0
        for i in range(60):
            code = ETABLISSEMENTS[i % 3][0]
            domaine, titre, mots = SUJETS[(i * 7) % len(SUJETS)]
            type_doc = "these" if (i // 3) % 3 != 2 else "memoire"
            annee = 2015 + (i * 5) % 11
            statut = "en_preparation" if annee >= 2025 and i % 2 == 0 else "soutenu"
            sous_nom, sous_type = FACULTES[code][i % len(FACULTES[code])]
            cle = PREFIXE + hashlib.sha1(f"{i}".encode()).hexdigest()[:15]
            if db.query(Document).filter(Document.zotero_item_key == cle).first():
                continue
            lieu = ["en Casamance", "dans la région de Thiès", "à Dakar", "dans le bassin arachidier",
                    "dans la vallée du fleuve Sénégal", "à Saint-Louis"][i % 6]
            titre_complet = f"{titre} {lieu}" if i % 4 else titre
            db.add(Document(
                titre=titre_complet, auteur=_personne(r), directeur=f"Pr {_personne(r)}",
                type=type_doc, statut=statut, annee=annee, domaine=domaine,
                etablissement_code=code, sous_entite_nom=sous_nom, sous_entite_type=sous_type,
                langue="français" if i % 5 else "anglais", mots_cles=mots,
                resume=(f"{MENTION} Ce travail étudie {titre_complet[0].lower()}{titre_complet[1:]}. "
                        "Il s'appuie sur une enquête de terrain et une revue de la littérature, "
                        "et propose des pistes pour les politiques publiques."),
                zotero_item_key=cle,
                numero_national=generer_numero(db, etablissement_code=code, type_doc=type_doc,
                                               statut=statut, annee=annee),
            ))
            db.flush()
            crees += 1
        acces_docs.recalculer(db)
        db.commit()
        recherche.indexer(db, db.query(Document).filter(Document.zotero_item_key.like(PREFIXE + "%")))
        print(f"{crees} notice(s) de démonstration ajoutée(s).")
    finally:
        db.close()


def retirer():
    db = SessionLocal()
    try:
        docs = db.query(Document).filter(Document.zotero_item_key.like(PREFIXE + "%")).all()
        for d in docs:
            db.add(DocumentRetire(
                document_id=d.id, numero_national=d.numero_national, titre=d.titre,
                auteur=d.auteur, type=d.type, annee=d.annee, etablissement_code=d.etablissement_code,
                zotero_item_key=d.zotero_item_key, raison="données de démonstration retirées",
            ))
            db.delete(d)
        db.commit()
        recherche.retirer([d.id for d in docs])
        print(f"{len(docs)} notice(s) de démonstration retirée(s). "
              "Les établissements créés sont conservés (à désactiver au besoin).")
    finally:
        db.close()


def etat():
    db = SessionLocal()
    try:
        n = db.query(Document).filter(Document.zotero_item_key.like(PREFIXE + "%")).count()
        print(f"{n} notice(s) de démonstration présente(s).")
    finally:
        db.close()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    {"installer": installer, "retirer": retirer, "etat": etat}.get(
        action, lambda: print(__doc__))()
