"""L'assainissement du HTML est la seule barrière entre l'éditeur de la
page « À propos » et un `<script>` servi à chaque visiteur. Il mérite
des cas explicites plutôt qu'un contrôle à l'œil.

    cd backend && python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.html_riche import nettoyer, en_texte  # noqa: E402


def test_contenu_legitime_conserve():
    brut = "<h2>Titre</h2><p>Un <strong>mot</strong> et une <em>nuance</em>.</p>"
    assert nettoyer(brut) == brut


def test_listes_et_citations_conservees():
    brut = "<ul><li>a</li><li>b</li></ul><blockquote>cité</blockquote>"
    assert nettoyer(brut) == brut


def test_script_supprime_avec_son_contenu():
    # Le texte d'un <script> n'est pas du texte : le conserver
    # afficherait « alert(1) » sur la page, au mieux.
    assert nettoyer("<script>alert(1)</script>bonjour") == "bonjour"
    assert "alert" not in nettoyer("<p>a</p><script>alert(1)</script>")


def test_gestionnaires_d_evenements_retires():
    for attribut in ("onclick", "onerror", "onload", "onmouseover"):
        sortie = nettoyer(f'<p {attribut}="alert(1)">x</p>')
        assert attribut not in sortie.lower()
        assert sortie == "<p>x</p>"


def test_schemas_dangereux_refuses():
    for url in ("javascript:alert(1)", "JaVaScRiPt:alert(1)",
                "data:text/html,<script>alert(1)</script>",
                "vbscript:msgbox", "java\tscript:alert(1)"):
        sortie = nettoyer(f'<a href="{url}">clic</a>')
        assert "href" not in sortie, url
        assert "clic" in sortie      # le texte reste, seul le lien saute


def test_schemas_legitimes_acceptes():
    for url in ("https://exemple.sn", "http://exemple.sn",
                "mailto:a@b.sn", "tel:+221330000000",
                "/recherche", "#ancre", "../page.html"):
        assert 'href="' in nettoyer(f'<a href="{url}">x</a>'), url


def test_nouvel_onglet_recoit_noopener():
    # Sans rel="noopener", la page ouverte garde la main sur la nôtre par
    # window.opener.
    sortie = nettoyer('<a href="https://x.sn" target="_blank">x</a>')
    assert 'rel="noopener noreferrer"' in sortie


def test_style_en_ligne_retire():
    # Un style en ligne suffit à recouvrir la page entière d'un calque.
    assert nettoyer('<div style="position:fixed;inset:0">x</div>') == "<div>x</div>"


def test_balises_inconnues_perdent_la_balise_pas_le_texte():
    assert nettoyer("<marquee>texte</marquee>") == "texte"
    assert nettoyer("<custom-el>texte</custom-el>") == "texte"


def test_document_desequilibre_est_referme():
    # Une balise laissée ouverte déborderait sur le reste de la page
    # publique, pied de page compris.
    sortie = nettoyer("<div><p>a")
    assert sortie.endswith("</p></div>")
    assert nettoyer("</p></div>orphelin") == "orphelin"


def test_commentaires_supprimes():
    assert nettoyer("<!--[if IE]><script>alert(1)</script><![endif]-->ok") == "ok"


def test_entites_deja_echappees_ne_sont_pas_doublement_decodees():
    assert nettoyer("<p>&lt;script&gt;</p>") == "<p>&lt;script&gt;</p>"


def test_svg_supprime_avec_son_contenu():
    assert nettoyer("<svg><script>alert(1)</script></svg>reste") == "reste"


def test_imbrication_excessive_bornee():
    profond = "<div>" * 200 + "texte" + "</div>" * 200
    sortie = nettoyer(profond)
    assert "texte" in sortie
    assert sortie.count("<div>") <= 20


def test_entree_vide_ou_absente():
    assert nettoyer("") == ""
    assert nettoyer(None) == ""


def test_longueur_bornee():
    enorme = "<p>a</p>" * 100_000
    assert len(nettoyer(enorme)) <= 250_000


def test_aucune_sortie_ne_contient_de_forme_executable():
    # Filet général : quelle que soit l'astuce d'encodage, ces formes ne
    # doivent jamais ressortir.
    pieges = [
        "<scr<script>ipt>alert(1)</script>",
        "<SCRIPT SRC=//x.sn/a.js></SCRIPT>",
        '<img src=x onerror="alert(1)">',
        "<iframe src=javascript:alert(1)></iframe>",
        "<object data=javascript:alert(1)></object>",
        "<embed src=//x.sn/a.swf>",
        "<a href=' javascript:alert(1)'>x</a>",
        "<form action=x><input name=y></form>",
    ]
    for p in pieges:
        sortie = nettoyer(p).lower()
        assert "<script" not in sortie, p
        assert "<iframe" not in sortie, p
        assert "onerror" not in sortie, p
        assert "javascript:" not in sortie, p


def test_apercu_texte_sans_balises():
    apercu = en_texte("<h2>Titre</h2><p>Du <b>texte</b>.</p>")
    assert "<" not in apercu
    assert "Titre" in apercu and "texte" in apercu
