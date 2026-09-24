"""Traduction de l'interface publique (app/core/i18n.py).

Le test principal vérifie que chaque chaîne marquée _() dans les
gabarits publics a sa traduction anglaise et portugaise : une chaîne
ajoutée sans traduction s'afficherait en français au milieu d'une page
anglaise.

    cd backend && python -m pytest tests/
"""
import glob
import os
import re
import sys
from types import SimpleNamespace

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from app.core import i18n  # noqa: E402

MOTIF = re.compile(r"""_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")
LIBELLES_INDEX = ["Documents indexés", "Établissements partenaires",
                  "Travaux soutenus", "En préparation"]


def _chaines_des_gabarits():
    fichiers = glob.glob(os.path.join(RACINE, "app/templates/public/**/*.html"), recursive=True)
    fichiers.append(os.path.join(RACINE, "app/templates/erreur.html"))
    chaines = set(LIBELLES_INDEX)
    for f in fichiers:
        with open(f, encoding="utf-8") as fh:
            for m in MOTIF.finditer(fh.read()):
                chaines.add(m.group(1) if m.group(1) is not None else m.group(2))
    return chaines


def test_toutes_les_chaines_sont_traduites():
    manquantes = sorted(c for c in _chaines_des_gabarits() if c not in i18n.TRADUCTIONS)
    assert not manquantes, "Sans traduction : " + " | ".join(manquantes)


def test_traductions_completes_et_variables_conservees():
    for fr, (en, pt) in i18n.TRADUCTIONS.items():
        assert en and pt, fr
        variables = set(re.findall(r"{(\w+)}", fr))
        assert set(re.findall(r"{(\w+)}", en)) == variables, fr
        assert set(re.findall(r"{(\w+)}", pt)) == variables, fr


def test_traduire():
    assert i18n.traduire("Rechercher", "en") == "Search"
    assert i18n.traduire("Rechercher", "pt") == "Pesquisar"
    assert i18n.traduire("Rechercher", "fr") == "Rechercher"
    assert i18n.traduire("Chaîne inconnue", "en") == "Chaîne inconnue"
    assert i18n.traduire("Page {n} sur {total}", "en", n=2, total=9) == "Page 2 of 9"


def _requete(query=None, cookie=None):
    return SimpleNamespace(query_params=query or {}, cookies={"lang": cookie} if cookie else {},
                           headers={"accept-language": "en-US,en;q=0.9"})


def test_langue_de():
    tout = {"langues_actives": '["fr","en","pt"]'}
    assert i18n.langue_de(_requete(), tout) == "fr"  # pas de bascule sur le navigateur
    assert i18n.langue_de(_requete(cookie="pt"), tout) == "pt"
    assert i18n.langue_de(_requete(query={"lang": "en"}, cookie="pt"), tout) == "en"
    assert i18n.langue_de(_requete(cookie="de"), tout) == "fr"
    # Langue désactivée dans les paramètres : ignorée
    assert i18n.langue_de(_requete(cookie="pt"), {"langues_actives": '["fr","en"]'}) == "fr"


def test_langues_actives():
    assert i18n.langues_actives({"langues_actives": '["en"]'}) == ["fr", "en"]
    assert i18n.langues_actives({"langues_actives": "pas du json"}) == ["fr"]
