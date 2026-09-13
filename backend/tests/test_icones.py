"""La police d'icônes est réduite aux icônes employées : une icône
ajoutée à un gabarit sans régénérer la police s'affiche en carré vide.

Le défaut est visible, mais seulement sur la page concernée — et il a
déjà échappé une fois à la relecture. Ce test le transforme en échec
franc, au lieu d'un petit carré qu'on finit par ne plus voir.

    cd backend && python -m pytest tests/

Quand il échoue :  python3 scripts/generer-icones.py
"""
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RACINE, "scripts"))

CSS_GENERE = os.path.join(
    RACINE, "backend/app/static/vendor/icones/tabler-icons.css"
)


def _icones_declarees() -> set:
    with open(CSS_GENERE, encoding="utf-8") as f:
        return set(re.findall(r"\.ti-([a-z0-9-]+):before", f.read()))


def _icones_citees() -> set:
    """Uniquement les écritures sans ambiguïté.

    Le motif large du générateur ramasse aussi des mots ordinaires ; on
    ne peut pas exiger qu'ils figurent dans la police.
    """
    motifs = [
        re.compile(r"\bti ti-([a-z0-9][a-z0-9-]*)"),
        re.compile(r"icone:\s*'([a-z0-9][a-z0-9-]*)'"),
    ]
    citees = set()
    for sous_dossier in ("templates", "static/js"):
        base = os.path.join(RACINE, "backend/app", sous_dossier)
        for dossier, _, fichiers in os.walk(base):
            if "vendor" in dossier:
                continue
            for nom in fichiers:
                if not nom.endswith((".html", ".js")):
                    continue
                with open(os.path.join(dossier, nom), encoding="utf-8") as f:
                    contenu = f.read()
                for motif in motifs:
                    citees |= set(motif.findall(contenu))
    return citees


def test_police_generee_presente():
    assert os.path.exists(CSS_GENERE), (
        "La feuille d'icônes n'existe pas : lancez scripts/generer-icones.py"
    )


def test_toute_icone_citee_est_dans_la_police():
    manquantes = sorted(_icones_citees() - _icones_declarees())
    assert not manquantes, (
        "Ces icônes sont employées mais absentes de la police réduite, "
        "elles s'afficheront en carré vide : "
        + ", ".join(manquantes)
        + "\nCorrectif : python3 scripts/generer-icones.py"
    )


def test_la_police_reste_reduite():
    # Garde-fou dans l'autre sens : si le motif large se met à tout
    # ramasser, la police enfle et l'intérêt du découpage disparaît.
    declarees = _icones_declarees()
    assert 0 < len(declarees) < 400, len(declarees)
    poids = os.path.getsize(
        os.path.join(RACINE, "backend/app/static/vendor/icones/tabler-icons.woff2")
    )
    assert poids < 120_000, f"{poids} octets — la police complète fait 827 Ko"
