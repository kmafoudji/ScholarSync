"""Page Aide publique : les trois langues gardent le même plan.

Le sommaire (public/aide.html) pointe vers des ancres fixes. Une section
supprimée ou renommée dans une seule langue laisserait un lien mort dans
cette langue-là, sans que rien ne le montre en français.

    cd backend && python -m pytest tests/
"""
import os
import re

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GABARITS = os.path.join(RACINE, "app/templates/public")


def _lire(chemin):
    with open(os.path.join(GABARITS, chemin), encoding="utf-8") as f:
        return f.read()


def _ancres_du_sommaire():
    return re.findall(r"\('([a-z]+)', '[a-z0-9-]+', _\(", _lire("aide.html"))


def _sections(langue):
    return re.findall(r'<section class="aide-section[^"]*" id="([a-z]+)"', _lire(f"aide/{langue}.html"))


def test_sommaire_complet():
    assert _ancres_du_sommaire() == _sections("fr")


def test_meme_plan_dans_les_trois_langues():
    for langue in ("en", "pt"):
        assert _sections(langue) == _sections("fr"), langue


def test_meme_nombre_de_questions():
    compte = {l: _lire(f"aide/{l}.html").count("<details>") for l in ("fr", "en", "pt")}
    assert len(set(compte.values())) == 1, compte


def test_lien_dans_le_menu():
    assert 'href="/aide"' in _lire("base.html")
