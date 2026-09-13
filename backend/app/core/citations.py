"""Export d'une notice aux formats bibliographiques.

Un catalogue de thèses sert d'abord à être cité : sans export, chaque
lecteur retape les métadonnées à la main, avec les fautes que cela
suppose. Trois sorties couvrent les usages :

* **BibTeX** pour les chaînes LaTeX ;
* **RIS** pour Zotero, Mendeley, EndNote ;
* **APA** en texte, à coller dans une bibliographie.

Rien n'est ici échappé pour le HTML : ces formats sont servis en
`text/plain`, et la citation APA affichée dans la page passe par
l'échappement normal de Jinja.
"""

import re
import unicodedata

# ── BibTeX ────────────────────────────────────────────────────────
# Ces caractères ont un sens pour TeX. Non protégés, ils cassent la
# compilation du document qui importe le fichier — un `&` dans un nom
# d'établissement suffit.
ECHAPPEMENTS_TEX = {
    "\\": r"\textbackslash{}",
    "{": r"\{", "}": r"\}",
    "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _tex(valeur) -> str:
    if valeur is None:
        return ""
    texte = str(valeur)
    for brut, protege in ECHAPPEMENTS_TEX.items():
        texte = texte.replace(brut, protege)
    # Les retours à la ligne n'ont pas de sens dans un champ BibTeX.
    return " ".join(texte.split())


def _cle_bibtex(doc) -> str:
    """Clé de citation : nom d'auteur, année, numéro national.

    Elle doit être unique dans une bibliographie et ne contenir que des
    caractères ASCII — « Ndiaye2024 » et « Ndiayé2024 » seraient deux
    clés différentes pour l'auteur, une seule pour BibTeX.
    """
    nom = (doc.auteur or "anon").split()[0]
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    nom = re.sub(r"[^A-Za-z0-9]", "", nom) or "anon"
    return f"{nom.lower()}{doc.annee or ''}{(doc.numero_national or '')[-4:]}"


def _type_bibtex(doc) -> str:
    # `phdthesis` et `mastersthesis` sont les deux types que BibTeX
    # connaît pour les travaux universitaires.
    return "phdthesis" if doc.type == "these" else "mastersthesis"


def bibtex(doc, etablissement_nom: str = None, base_url: str = "") -> str:
    champs = [
        ("title", doc.titre),
        ("author", doc.auteur),
        ("year", doc.annee),
        ("school", etablissement_nom or doc.etablissement_code),
        ("type", "Thèse de doctorat" if doc.type == "these" else "Mémoire de master"),
        ("address", None),
        ("language", doc.langue),
        ("keywords", ", ".join(doc.mots_cles) if doc.mots_cles else None),
        ("abstract", doc.resume),
        ("note", f"Numéro national : {doc.numero_national}"),
        ("url", doc.url_document or (f"{base_url}/document/{doc.id}" if base_url else None)),
    ]
    if doc.sous_entite_nom:
        champs.insert(5, ("institution", doc.sous_entite_nom))
    if doc.directeur:
        champs.insert(2, ("supervisor", doc.directeur))

    lignes = [f"@{_type_bibtex(doc)}{{{_cle_bibtex(doc)},"]
    for nom, valeur in champs:
        if valeur in (None, "", []):
            continue
        lignes.append(f"  {nom:<12}= {{{_tex(valeur)}}},")
    # Retirer la virgule de la dernière ligne : certains analyseurs
    # anciens s'en étranglent.
    if len(lignes) > 1:
        lignes[-1] = lignes[-1].rstrip(",")
    lignes.append("}")
    return "\n".join(lignes) + "\n"


# ── RIS ───────────────────────────────────────────────────────────
def _ris_ligne(balise: str, valeur) -> str:
    if valeur in (None, "", []):
        return ""
    # Une valeur sur plusieurs lignes romprait le format : chaque ligne
    # RIS commence par une balise de deux lettres.
    return f"{balise}  - {' '.join(str(valeur).split())}\n"


def ris(doc, etablissement_nom: str = None, base_url: str = "") -> str:
    sortie = "TY  - THES\n"
    sortie += _ris_ligne("TI", doc.titre)
    sortie += _ris_ligne("AU", doc.auteur)
    if doc.directeur:
        sortie += _ris_ligne("A2", doc.directeur)
    sortie += _ris_ligne("PY", doc.annee)
    sortie += _ris_ligne("PB", etablissement_nom or doc.etablissement_code)
    if doc.sous_entite_nom:
        sortie += _ris_ligne("AD", doc.sous_entite_nom)
    sortie += _ris_ligne("M3", "Thèse de doctorat" if doc.type == "these"
                         else "Mémoire de master")
    sortie += _ris_ligne("LA", doc.langue)
    for mot in (doc.mots_cles or []):
        sortie += _ris_ligne("KW", mot)
    sortie += _ris_ligne("AB", doc.resume)
    sortie += _ris_ligne("SN", doc.numero_national)
    sortie += _ris_ligne("UR", doc.url_document
                         or (f"{base_url}/document/{doc.id}" if base_url else None))
    sortie += "ER  - \n"
    return sortie


# ── APA ───────────────────────────────────────────────────────────
def apa(doc, etablissement_nom: str = None) -> str:
    """Référence au style APA 7, forme « thèse non publiée ».

    Les notices viennent de Zotero où l'auteur est saisi « NDIAYE
    Aminata » : on ne tente pas d'en déduire une initiale, au risque de
    produire « A. Aminata ». Le nom est repris tel quel — une citation
    juste et un peu longue vaut mieux qu'une citation élégante et fausse.
    """
    genre = ("Thèse de doctorat" if doc.type == "these" else "Mémoire de master")
    etab = etablissement_nom or doc.etablissement_code
    morceaux = [
        f"{doc.auteur} ({doc.annee}).",
        f"{doc.titre}",
        f"[{genre}, {etab}].",
    ]
    if doc.numero_national:
        morceaux.append(f"N° national {doc.numero_national}.")
    if doc.url_document:
        morceaux.append(doc.url_document)
    return " ".join(m for m in morceaux if m)


def nom_fichier(doc, extension: str) -> str:
    """Nom de fichier sûr, dérivé du numéro national.

    Le titre ferait un nom plus parlant mais contient accents, points et
    barres obliques ; le numéro national est déjà un identifiant.
    """
    base = re.sub(r"[^A-Za-z0-9_-]", "", doc.numero_national or "notice")
    return f"{base or 'notice'}.{extension}"
