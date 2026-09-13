#!/usr/bin/env python3
"""Fabrique une police d'icônes réduite aux icônes réellement employées.

La police Tabler complète pèse 827 Ko pour environ 5 000 icônes ; l'outil
en emploie moins de cent. Le reste est payé par chaque visiteur, sur des
connexions qui ne sont pas toutes rapides. Ce script relit les gabarits,
relève les icônes citées, et n'embarque que celles-là — police et
feuille de style.

    npm install @tabler/icons-webfont@3.8.0
    python3 scripts/generer-icones.py

À relancer après avoir introduit une icône nouvelle. Une icône oubliée
se voit tout de suite à l'écran (carré vide), elle ne se dégrade pas en
silence.
"""
import os
import re
import subprocess
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = [
    os.path.join(RACINE, "backend/app/templates"),
    os.path.join(RACINE, "backend/app/static/js"),
    os.path.join(RACINE, "backend/app/static/css"),
]
PAQUET = os.path.join(RACINE, "node_modules/@tabler/icons-webfont/dist")
SORTIE = os.path.join(RACINE, "backend/app/static/vendor/icones")

# Trois écritures cohabitent : `ti ti-nom` dans le HTML, `icone: 'nom'`
# dans le JavaScript qui compose la classe à la volée, et un nom passé en
# argument de macro Jinja — `champ('user-check', 'Direction', …)`. Cette
# troisième forme avait d'abord été oubliée, et l'icône correspondante
# manquait à la police sans que rien ne le signale.
#
# Le troisième motif relève donc toute chaîne entre guillemets qui a la
# forme d'un nom d'icône, puis on la recoupe avec la table des noms
# Tabler. Le recoupement rend l'excès sans conséquence : un mot qui n'est
# pas une icône est écarté, et un mot qui se trouve en être une ajoute
# deux cents octets à la police. C'est le bon sens de l'erreur — une
# icône en trop ne se voit pas, une icône manquante s'affiche en carré
# vide.
MOTIFS = [
    re.compile(r"\bti-([a-z0-9][a-z0-9-]*)"),
    re.compile(r"icone:\s*'([a-z0-9][a-z0-9-]*)'"),
    re.compile(r"['\"]([a-z][a-z0-9]*(?:-[a-z0-9]+)*)['\"]"),
]


def icones_employees(larges: bool = True) -> set:
    """Noms d'icônes cités dans les sources.

    `larges=False` limite la relève aux écritures sans ambiguïté
    (`ti-nom`, `icone: 'nom'`), pour distinguer un vrai nom mal
    orthographié d'un mot ordinaire ramassé par le motif général.
    """
    motifs = MOTIFS if larges else MOTIFS[:2]
    trouvees = set()
    for racine in SOURCES:
        for dossier, _, fichiers in os.walk(racine):
            if "vendor" in dossier:
                continue
            for nom in fichiers:
                if not nom.endswith((".html", ".js", ".css")):
                    continue
                with open(os.path.join(dossier, nom), encoding="utf-8") as f:
                    contenu = f.read()
                for motif in motifs:
                    trouvees |= set(motif.findall(contenu))
    # `ti` seul est la classe de base, pas une icône.
    return {n for n in trouvees if n not in ("ti",)}


def table_des_codes(css: str) -> dict:
    """Associe chaque nom d'icône à son point de code."""
    codes = {}
    for nom, code in re.findall(r"\.ti-([a-z0-9-]+):before\s*\{\s*content:\s*[\"']\\([0-9a-fA-F]+)[\"']", css):
        codes[nom] = int(code, 16)
    return codes


def main():
    css_source = os.path.join(PAQUET, "tabler-icons.min.css")
    woff_source = os.path.join(PAQUET, "fonts", "tabler-icons.woff2")
    if not os.path.exists(css_source):
        sys.exit("Paquet absent : npm install @tabler/icons-webfont@3.8.0")

    with open(css_source, encoding="utf-8") as f:
        codes = table_des_codes(f.read())

    voulues = icones_employees()
    connues = {n: codes[n] for n in sorted(voulues) if n in codes}

    # Seules les formes explicites méritent un avertissement : le motif
    # large ramène forcément des mots qui ne sont pas des icônes.
    explicites = icones_employees(larges=False)
    manquantes = sorted(explicites - set(codes))
    if manquantes:
        print("⚠ icônes inconnues du paquet (vérifiez le nom) :", ", ".join(manquantes))

    os.makedirs(SORTIE, exist_ok=True)

    # ── Police réduite ──────────────────────────────────────────
    points = ",".join(f"U+{c:04X}" for c in connues.values())
    subprocess.run([
        sys.executable, "-m", "fontTools.subset", woff_source,
        f"--unicodes={points}",
        "--flavor=woff2",
        # Les tables de mise en forme OpenType servent aux ligatures de
        # noms (« ti-home » écrit en toutes lettres) ; on n'emploie que
        # les points de code, et celle de Tabler est mal formée — la
        # garder fait échouer le découpage.
        "--layout-features=",
        "--drop-tables+=GSUB,GPOS",
        "--desubroutinize",
        f"--output-file={os.path.join(SORTIE, 'tabler-icons.woff2')}",
    ], check=True)

    # ── Feuille de style réduite ────────────────────────────────
    regles = "".join(
        f".ti-{nom}:before{{content:'\\{code:x}'}}" for nom, code in connues.items()
    )
    css = (
        "/* Police d'icônes Tabler (MIT) réduite aux icônes employées.\n"
        "   Fichier produit par scripts/generer-icones.py — ne pas modifier à la main. */\n"
        "@font-face{font-family:'tabler-icons';font-style:normal;font-weight:400;"
        "font-display:block;src:url('tabler-icons.woff2') format('woff2')}"
        ".ti{font-family:'tabler-icons'!important;speak:never;font-style:normal;"
        "font-weight:400;font-variant:normal;text-transform:none;line-height:1;"
        "-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;"
        "display:inline-block;vertical-align:middle}\n" + regles + "\n"
    )
    with open(os.path.join(SORTIE, "tabler-icons.css"), "w", encoding="utf-8") as f:
        f.write(css)

    taille_police = os.path.getsize(os.path.join(SORTIE, "tabler-icons.woff2"))
    print(f"{len(connues)} icônes · police {taille_police // 1024} Ko · "
          f"style {len(css) // 1024} Ko")


if __name__ == "__main__":
    main()
