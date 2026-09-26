"""Domaines REESAO : reconnaissance et ordre de priorité (app/services/domaines.py).

    cd backend && python -m pytest tests/
"""
import os
import sys
from types import SimpleNamespace as Doc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import permissions as p  # noqa: E402
from app.services import domaines as d  # noqa: E402


def test_huit_domaines_trilingues():
    assert len(d.DOMAINES) == 8
    for code, libelles in d.DOMAINES.items():
        assert len(libelles) == 3 and all(libelles), code
    assert d.libelle("SEG", "en") == "Economics and management"
    assert d.libelle("SEG") == "Sciences économiques et de gestion"


def test_propositions_d_apres_le_nom():
    assert d.proposer("Faculté de Médecine, de Pharmacie et d'Odontologie") == "SS"
    assert d.proposer("FASEG") == "SEG"
    assert d.proposer("Faculté des Sciences et Techniques") == "ST"
    assert d.proposer("Faculté des Sciences Juridiques et Politiques") == "SJPA"
    assert d.proposer("FASTEF") == "SEF"
    assert d.proposer("Faculté des Lettres et Sciences Humaines") == d.MULTI
    assert d.proposer("École doctorale ETHOS") is None


def test_metadonnee():
    assert d.depuis_metadonnee("SEG", {}) == "SEG"
    assert d.depuis_metadonnee("seg", {}) == "SEG"
    assert d.depuis_metadonnee("Sciences de la santé", {}) == "SS"
    assert d.depuis_metadonnee("Économie", {}) == "SEG"
    assert d.depuis_metadonnee("Environnement", {}) is None
    assert d.depuis_metadonnee("Environnement", {"environnement": "ST"}) == "ST"
    assert d.depuis_metadonnee("", {}) is None


def test_ordre_de_priorite():
    ratt = {("UCAD", "FASEG"): "SEG", ("UCAD", "ED ETHOS"): d.MULTI}
    doc = Doc(domaine_manuel=None, domaine="Droit", etablissement_code="UCAD", sous_entite_nom="FASEG")
    assert d.resoudre(doc, {}, ratt) == ("SJPA", "metadonnee")        # la notice prime
    doc.domaine_manuel = "SHS"
    assert d.resoudre(doc, {}, ratt) == ("SHS", "manuel")             # le choix manuel prime
    doc = Doc(domaine_manuel=None, domaine="Microfinance", etablissement_code="UCAD", sous_entite_nom="FASEG")
    assert d.resoudre(doc, {}, ratt) == ("SEG", "faculte")            # sinon la faculté
    doc.sous_entite_nom = "ED ETHOS"
    assert d.resoudre(doc, {}, ratt) == (None, None)                  # plusieurs domaines : non classé
    doc.sous_entite_nom = "Inconnue"
    assert d.resoudre(doc, {}, ratt) == (None, None)


def test_droits():
    admin = Doc(role="admin_etablissement", etablissement_code="UCAD")
    lecteur = Doc(role="lecteur", etablissement_code="UCAD")
    doc = "/admin/documents/0b9f3c4e-8a0d-4f4e-9c55-3f7a1e2b6d11/domaine"
    assert p.autorise(admin, "GET", "/admin/domaines")
    assert p.autorise(admin, "POST", "/admin/domaines/rattacher")
    assert p.autorise(admin, "POST", doc)
    assert not p.autorise(admin, "POST", "/admin/domaines/correspondance")  # tout le catalogue
    assert p.autorise(lecteur, "GET", "/admin/domaines")
    assert not p.autorise(lecteur, "POST", "/admin/domaines/rattacher")
