"""Règles d'accès : la plus fine l'emporte (app/services/acces.py).

    cd backend && python -m pytest tests/
"""
import os
import sys
import uuid
from types import SimpleNamespace as N

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.acces import Regles, url_publique  # noqa: E402


def doc(code="UCAD", type_="these", sous=None, acces="public", url="https://depot/x.pdf"):
    return N(id=uuid.uuid4(), etablissement_code=code, type=type_, sous_entite_nom=sous,
             acces=acces, url_document=url)


def regle(niveau, reference, acces):
    return N(niveau=niveau, reference=reference, acces=acces)


def test_sans_regle_public():
    assert Regles([]).acces(doc()) == "public"


def test_etablissement():
    r = Regles([regle("etablissement", "ucad", "restreint")])
    assert r.acces(doc("UCAD")) == "restreint"
    assert r.acces(doc("UGB")) == "public"


def test_collection_ecritures_variees():
    for ref in ("Thèses", "theses", "THESE", "UCAD/Thèses"):
        r = Regles([regle("collection", ref, "restreint")])
        assert r.acces(doc(type_="these")) == "restreint", ref
        assert r.acces(doc(type_="memoire")) == "public", ref


def test_collection_prefixee_limitee_a_son_etablissement():
    r = Regles([regle("collection", "UGB/Mémoires", "restreint")])
    assert r.acces(doc("UCAD", "memoire")) == "public"
    assert r.acces(doc("UGB", "memoire")) == "restreint"


def test_sous_collection_insensible_accents_et_casse():
    r = Regles([regle("sous_collection", "faculte des sciences", "restreint")])
    assert r.acces(doc(sous="Faculté des Sciences")) == "restreint"


def test_la_plus_fine_l_emporte():
    d = doc(sous="FASTEF")
    regles = [
        regle("etablissement", "UCAD", "restreint"),
        regle("collection", "Thèses", "public"),
    ]
    assert Regles(regles).acces(d) == "public"                       # collection > établissement
    regles.append(regle("sous_collection", "FASTEF", "restreint"))
    assert Regles(regles).acces(d) == "restreint"                    # sous-collection > collection
    regles.append(regle("document", str(d.id), "public"))
    assert Regles(regles).acces(d) == "public"                       # document > tout


def test_forme_prefixee_prime_sur_forme_simple():
    r = Regles([regle("sous_collection", "FASTEF", "public"),
                regle("sous_collection", "UCAD/FASTEF", "restreint")])
    assert r.acces(doc(sous="FASTEF")) == "restreint"


def test_url_masquee_si_restreint():
    assert url_publique(doc(acces="public")) == "https://depot/x.pdf"
    assert url_publique(doc(acces="restreint")) is None
