"""Recherche avancée : lecture des critères, ET / OU (app/services/recherche_avancee.py).

    cd backend && python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import i18n  # noqa: E402
from app.services import recherche_avancee as ra  # noqa: E402


def test_lire_ignore_les_lignes_vides_et_les_champs_inconnus():
    criteres, op = ra.lire(["titre", "pirate", "auteur"], ["  eau   douce ", "riz", ""], "ou")
    assert criteres == [("titre", "eau douce"), ("tout", "riz")]
    assert op == "ou"
    assert ra.lire(["titre"], ["x"], "n'importe quoi")[1] == "et"


def test_lire_borne_nombre_et_longueur():
    criteres, _ = ra.lire(["titre"] * 30, ["x" * 500] * 30)
    assert len(criteres) == ra.MAX_CRITERES
    assert len(criteres[0][1]) == ra.LONGUEUR_MAX


def test_numero_ancienne_forme_normalise():
    criteres, _ = ra.lire(["numero"], ["SCUCTS2016000192"])
    assert criteres == [("numero", "SNUCT2016000192")]


def test_aller_retour_filtres():
    criteres, op = ra.lire(["titre", "auteur"], ["eau", "diop"], "ou")
    filtres = ra.vers_filtres(criteres, op)
    assert filtres == {"champ": ["titre", "auteur"], "terme": ["eau", "diop"], "op": "ou"}
    assert ra.depuis_filtres(filtres) == (criteres, op)
    assert ra.vers_filtres([], "et") == {}
    assert "op" not in ra.vers_filtres(criteres, "et")


def test_combiner_et_ou():
    a, b = ["1", "2", "3"], ["3", "1", "9"]
    assert ra.combiner([a, b], "et") == ["1", "3"]      # ordre de la 1re liste
    assert ra.combiner([a, b], "ou") == ["1", "2", "3", "9"]
    assert ra.combiner([], "et") == []


def test_ids_moteur_limite_chaque_critere_a_ses_champs():
    appels = []

    def chercher(texte, attributs, tous):
        appels.append((texte, attributs, tous))
        return {"eau": ["1", "2"], "diop": ["2", "3"]}[texte]

    ids = ra.ids_moteur([("titre", "eau"), ("auteur", "diop")], "et", chercher)
    assert ids == ["2"]
    assert appels == [("eau", ["titre"], True), ("diop", ["auteur"], True)]
    assert ra.ids_moteur([("tout", "eau")], "et", lambda *a: None) is None


def test_libelles_traduits():
    for libelle, _ in ra.CHAMPS.values():
        assert libelle in i18n.TRADUCTIONS, libelle
