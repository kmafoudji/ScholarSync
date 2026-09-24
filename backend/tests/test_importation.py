"""Import CSV / RIS / BibTeX (app/services/importation.py).

    cd backend && python -m pytest tests/
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import importation as imp  # noqa: E402

CSV = """titre;auteur;type;statut;année;directeur;domaine;faculté;langue;résumé;mots-clés;url
La gouvernance locale;NDIAYE Aminata;Thèse;soutenu;2021;FALL Moussa;Science politique;ED ETHOS;français;Résumé;"décentralisation; collectivités";https://depot/1
Sols de la vallée;SOW Awa;mémoire;en préparation;2025;;Agronomie;UFR S2ATA;;;;
;Sans titre;thèse;soutenu;2020;;;;;;;
Doublon;X;thèse;soutenu;2020;;;;;;;
Doublon;X;thèse;soutenu;2020;;;;;;;
Livre;Y;roman;soutenu;2020;;;;;;;
"""

RIS = """TY  - THES
TI  - Hydrologie du fleuve
AU  - DIOP Cheikh
PY  - 2019
M3  - Thèse de doctorat
KW  - eau
KW  - bassin
AB  - Un résumé.
UR  - https://depot/2
ER  -
TY  - THES
TI  - Mémoire sur le riz
AU  - BA Fatou
PY  - 2022
M3  - Mémoire de master
N1  - en préparation
ER  -
"""

BIB = r"""
@phdthesis{diop2019,
  title = {Les {\'E}tats et la {mer}},
  author = {Diop, Cheikh and Fall, Awa},
  year = 2019,
  school = "UCAD",
  keywords = {mer, droit},
  abstract = {Texte, avec une virgule.},
  url = {https://depot/3}
}
@mastersthesis{ba2022, title="Le riz", author="Ba, Fatou", year="2022"}
@comment{ignoré}
"""


def test_csv():
    fmt, n = imp.lire("liste.csv", CSV.encode("utf-8"))
    assert fmt == "csv"
    a, b, sans_titre, d1, d2, livre = n
    assert a.valide and a.type == "these" and a.annee == 2021 and a.faculte == "ED ETHOS"
    assert a.mots_cles == ["décentralisation", "collectivités"] and a.url == "https://depot/1"
    assert b.valide and b.type == "memoire" and b.statut == "en_preparation"
    assert "titre manquant" in sans_titre.erreurs
    assert d1.valide and not d2.valide and "doublon" in d2.erreurs[0]
    assert not livre.valide
    assert a.cle == imp.lire("x.csv", CSV.encode())[1][0].cle  # clé stable


def test_csv_windows_et_virgules():
    texte = "Titre,Auteur,Type,Annee\nÉtude,Sy,these,2018\n"
    fmt, n = imp.lire("x.csv", texte.encode("cp1252"))
    assert n[0].valide and n[0].titre == "Étude"


def test_ris():
    fmt, n = imp.lire("export.ris", RIS.encode())
    assert fmt == "ris" and len(n) == 2
    assert n[0].valide and n[0].type == "these" and n[0].mots_cles == ["eau", "bassin"]
    assert n[1].type == "memoire" and n[1].statut == "en_preparation"


def test_bibtex():
    fmt, n = imp.lire("refs.bib", BIB.encode())
    assert fmt == "bibtex" and len(n) == 2
    assert n[0].valide and n[0].type == "these" and n[0].annee == 2019
    assert n[0].auteur == "Diop, Cheikh; Fall, Awa"
    assert n[0].resume == "Texte, avec une virgule."
    assert n[1].type == "memoire" and n[1].titre == "Le riz"


def test_type_par_defaut():
    _, n = imp.lire("x.csv", "titre;auteur;annee\nA;B;2020\n".encode(), type_defaut="memoire")
    assert n[0].valide and n[0].type == "memoire"


def test_refus():
    with pytest.raises(imp.ImportRefuse):
        imp.lire("x.pdf", b"%PDF-1.4")
    with pytest.raises(imp.ImportRefuse):
        imp.lire("x.csv", b"")
    with pytest.raises(imp.ImportRefuse):
        imp.lire("x.csv", "nom;prenom\na;b\n".encode())


def test_modele_relu():
    _, n = imp.lire("modele.csv", imp.modele_csv().encode("utf-8"))
    assert all(x.valide for x in n) and len(n) == 2
