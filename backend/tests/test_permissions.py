"""Droits par rôle dans l'administration (app/core/permissions.py).

    cd backend && python -m pytest tests/
"""
import os
import sys
from types import SimpleNamespace as U

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import permissions as p  # noqa: E402

SUPER = U(role="super_admin", etablissement_code=None)
ADMIN = U(role="admin_etablissement", etablissement_code="UCAD")
ANCIEN = U(role="gestionnaire_bu", etablissement_code="UGB")
LECTEUR = U(role="lecteur", etablissement_code="UCAD")
ORPHELIN = U(role="admin_etablissement", etablissement_code=None)
INCONNU = U(role="pirate", etablissement_code="UCAD")

DOC = "/admin/documents/0b9f3c4e-8a0d-4f4e-9c55-3f7a1e2b6d11/toggle-acces"

RESERVEES = [
    ("GET", "/admin/zotero"), ("POST", "/admin/zotero/ajouter"),
    ("POST", "/admin/zotero/3/supprimer"), ("POST", "/admin/sync/lancer"),
    ("GET", "/admin/utilisateurs"), ("POST", "/admin/utilisateurs/ajouter"),
    ("GET", "/admin/parametres/identite"), ("POST", "/admin/parametres/identite"),
    ("POST", "/admin/parametres/smtp"),
    ("GET", "/admin/etablissements"), ("POST", "/admin/etablissements/2/modifier"),
    ("POST", "/admin/parametres/sync-intervalle"),
    ("GET", "/admin/une-route-ajoutee-demain"),
]


def test_super_admin_a_tout():
    for methode, chemin in RESERVEES + [("POST", DOC)]:
        assert p.autorise(SUPER, methode, chemin)


def test_etablissement_exclu_des_routes_reservees():
    for u in (ADMIN, ANCIEN, LECTEUR):
        for methode, chemin in RESERVEES:
            assert not p.autorise(u, methode, chemin), (u.role, methode, chemin)


def test_etablissement_accede_a_son_espace():
    for methode, chemin in [
        ("GET", "/admin"), ("GET", "/admin/documents"), ("POST", DOC),
        ("GET", "/admin/sync"), ("GET", "/admin/sync/etat"),
        ("POST", "/admin/sync/lancer/4"), ("GET", "/admin/mon-etablissement"),
        ("POST", "/admin/mon-etablissement"), ("GET", "/admin/exports/documents"),
        ("GET", "/admin/acces"), ("POST", "/admin/acces/definir"),
        ("POST", "/admin/acces/12/supprimer"),
    ]:
        assert p.autorise(ADMIN, methode, chemin), chemin
        assert p.autorise(ANCIEN, methode, chemin), chemin


def test_lecteur_ne_modifie_rien():
    assert p.autorise(LECTEUR, "GET", "/admin/mon-etablissement")
    assert p.autorise(LECTEUR, "HEAD", "/admin/documents")
    assert not p.autorise(LECTEUR, "POST", "/admin/mon-etablissement")
    assert not p.autorise(LECTEUR, "POST", "/admin/sync/lancer/4")
    assert not p.autorise(LECTEUR, "POST", DOC)
    assert p.autorise(LECTEUR, "GET", "/admin/acces")
    assert not p.autorise(LECTEUR, "POST", "/admin/acces/definir")


def test_role_inconnu_na_rien():
    assert not p.autorise(INCONNU, "GET", "/admin")


def test_chemins_voisins_non_ouverts():
    assert not p.autorise(ADMIN, "POST", "/admin/sync/lancer/4/../../zotero/1/supprimer")
    assert not p.autorise(ADMIN, "POST", "/admin/sync/lancer/abc")
    assert not p.autorise(ADMIN, "GET", "/admin/documentsX")


def test_perimetre():
    assert p.perimetre(SUPER) is None
    assert p.perimetre(ADMIN) == "UCAD"
    assert p.perimetre(ANCIEN) == "UGB"
    # Sans établissement rattaché : rien, et surtout pas tout
    assert p.perimetre(ORPHELIN) == p.AUCUN


def test_chacun_gere_son_compte():
    for u in (SUPER, ADMIN, ANCIEN, LECTEUR, ORPHELIN):
        assert p.autorise(u, "GET", "/admin/mon-compte")
        assert p.autorise(u, "POST", "/admin/mon-compte")
        assert p.autorise(u, "POST", "/admin/mon-compte/mot-de-passe")
    assert not p.autorise(INCONNU, "GET", "/admin/mon-compte")
    assert not p.autorise(LECTEUR, "POST", "/admin/mon-compte/autre-chose")
