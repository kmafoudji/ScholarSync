"""Numérotation nationale : format, clé de contrôle, continuité des rangs.

Le service a déjà cassé sans bruit : il importait un module inexistant
et produisait un format que la base refusait. La sync comptait chaque
nouvelle notice en erreur tout en affichant « succès ».

    cd backend && python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import numerotation as num  # noqa: E402


# Numéros réellement attribués en production (septembre 2026)
NUMEROS_PRODUCTION = ["SCUCMS2007000176", "SCUCTS2016000192", "SCUCTS2026000102"]


def test_les_numeros_existants_restent_valides():
    for n in NUMEROS_PRODUCTION:
        assert num.valider_numero(n), n


def test_format_16_caracteres_compatible_avec_la_colonne():
    n = num.composer_numero("UC", "these", "soutenu", 2016, 1)
    assert n == "SCUCTS2016000192"
    assert len(n) == num.LONGUEUR == 16
    assert len(n) <= 20  # documents.numero_national VARCHAR(20)


def test_cle_detecte_une_faute_de_frappe():
    assert not num.valider_numero("SCUCTS2016000193")  # clé fausse
    assert not num.valider_numero("SCUCTS2016000292")  # rang modifié
    assert not num.valider_numero("SC-UC-T-S-2016-0001-92")  # ancien format
    assert not num.valider_numero("")


def test_code_etablissement():
    assert num.code_etablissement("UCAD") == "UC"
    assert num.code_etablissement("ESP") == "ES"
    assert num.code_etablissement("X") == "XX"


# ── generer_numero, avec une session factice ──────────────────────────

class _Requete:
    def __init__(self, resultat, lignes=()):
        self._resultat, self._lignes = resultat, list(lignes)

    def filter_by(self, **_):
        return self

    def filter(self, *_):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._resultat

    def all(self):
        return self._lignes


class _Session:
    def __init__(self, compteur=None, numeros=()):
        self.compteur, self.numeros, self.ajouts = compteur, numeros, []

    def query(self, cible):
        if cible is num.NumerotationCompteur:
            return _Requete(self.compteur)
        return _Requete(None, [(n,) for n in self.numeros])

    def add(self, obj):
        self.ajouts.append(obj)

    def flush(self):
        pass


def test_premier_numero_sans_compteur_ni_document():
    db = _Session()
    n = num.generer_numero(db, "UCAD", "these", "soutenu", 2030)
    assert n[10:14] == "0001" and num.valider_numero(n)


def test_compteur_absent_mais_numeros_existants_pas_de_doublon():
    """Cas de la production : 4 documents, compteurs peut-être vides."""
    db = _Session(numeros=["SCUCTS2026000102"])
    n = num.generer_numero(db, "UCAD", "these", "soutenu", 2026)
    assert n[10:14] == "0002"
    assert n not in NUMEROS_PRODUCTION


def test_compteur_en_retard_sur_la_base():
    compteur = num.NumerotationCompteur(
        etablissement_code="UCAD", type="these", annee=2026, compteur=0
    )
    db = _Session(compteur=compteur, numeros=["SCUCTS2026000102"])
    n = num.generer_numero(db, "UCAD", "these", "soutenu", 2026)
    assert n.startswith("SCUCTS20260002")
    assert compteur.compteur == 2


def test_pas_de_numero_avant_la_soutenance():
    compteur = num.NumerotationCompteur(
        etablissement_code="UCAD", type="these", annee=2026, compteur=5
    )
    db = _Session(compteur=compteur)
    assert num.generer_numero(db, "UCAD", "these", "en_preparation", 2026) is None
    assert compteur.compteur == 5  # aucun rang consommé


def test_code_numero_gere_en_base():
    class Etab:
        code_numero = "K7"
    class Session2(_Session):
        def query(self, cible):
            if cible is num.Etablissement:
                return _Requete(Etab())
            return super().query(cible)
    n = num.generer_numero(Session2(), "NOUVELLE", "these", "soutenu", 2030)
    assert n.startswith("SCK7TS2030") and num.valider_numero(n)


def test_code_par_defaut():
    assert num.code_par_defaut("UCAD") == "UC"
    assert num.code_par_defaut("ISM-Dakar") == "IS"
    assert num.code_par_defaut("X") == "XX"
