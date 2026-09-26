"""
Import de notices hors Zotero : CSV, RIS, BibTeX.

Pour les universités qui ne cataloguent pas dans Zotero. Koha, PMB,
EndNote, Mendeley, JabRef ou un simple tableur savent tous produire au
moins un de ces trois formats : c'est la porte d'entrée commune, sans
connecteur propre à chaque logiciel.

Ce module ne touche pas à la base : il lit un fichier et rend une liste
de notices normalisées, avec les erreurs ligne par ligne. L'écriture se
fait après confirmation, dans main.py (analyser → aperçu → confirmer).

Une notice importée est identifiée par une clé stable dérivée du titre,
de l'auteur et de l'année (« imp-… ») : réimporter le même fichier met à
jour les notices au lieu de les dupliquer.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field

TAILLE_MAX_MO = 5
LIGNES_MAX = 5000

COLONNES_CSV = [
    "titre", "auteur", "type", "statut", "annee", "directeur", "domaine_reesao",
    "discipline", "faculte", "langue", "resume", "mots_cles", "url",
]

# En-têtes acceptés (normalisés sans accents ni casse) → champ
ALIAS = {
    "titre": "titre", "title": "titre", "intitule": "titre",
    "auteur": "auteur", "author": "auteur", "auteurs": "auteur", "nom": "auteur",
    "type": "type", "type de document": "type", "nature": "type",
    "statut": "statut", "status": "statut", "etat": "statut",
    "annee": "annee", "year": "annee", "date": "annee", "annee de soutenance": "annee",
    "directeur": "directeur", "direction": "directeur", "supervisor": "directeur",
    "directeur de these": "directeur", "encadrant": "directeur",
    "domaine": "domaine", "discipline": "domaine", "specialite": "domaine",
    "domaine reesao": "domaine_reesao", "reesao": "domaine_reesao",
    "code reesao": "domaine_reesao",
    "faculte": "faculte", "ecole doctorale": "faculte", "ufr": "faculte",
    "departement": "faculte", "sous entite": "faculte",
    "langue": "langue", "language": "langue",
    "resume": "resume", "abstract": "resume",
    "mots cles": "mots_cles", "mots-cles": "mots_cles", "keywords": "mots_cles",
    "url": "url", "lien": "url", "adresse": "url",
}


class ImportRefuse(ValueError):
    """Fichier illisible dans son ensemble (format, taille, encodage)."""


@dataclass
class Notice:
    ligne: int
    titre: str = ""
    auteur: str = ""
    type: str = ""            # these | memoire
    statut: str = "soutenu"   # soutenu | en_preparation
    annee: int | None = None
    directeur: str | None = None
    domaine: str | None = None
    domaine_reesao: str | None = None   # code REESAO (SS, ST, SEG…)
    faculte: str | None = None
    langue: str | None = None
    resume: str | None = None
    mots_cles: list = field(default_factory=list)
    url: str | None = None
    erreurs: list = field(default_factory=list)

    @property
    def valide(self) -> bool:
        return not self.erreurs

    @property
    def cle(self) -> str:
        base = "|".join(_norm(x) for x in (self.titre, self.auteur, str(self.annee or "")))
        return "imp-" + hashlib.sha1(base.encode()).hexdigest()[:16]


def _norm(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", (texte or "").strip().lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(re.sub(r"[_\-]+", " ", texte).split())


def _type(valeur: str) -> str:
    v = _norm(valeur)
    if not v:
        return ""
    if v.startswith(("these", "thesis", "phd", "doctor", "doctorat")) or "doctorat" in v:
        return "these"
    if v.startswith(("memoire", "master", "mastersthesis", "dissertation")) or "master" in v:
        return "memoire"
    return "?"


def _statut(valeur: str) -> str:
    v = _norm(valeur)
    if not v or v.startswith(("soutenu", "defended", "oui", "yes")):
        return "soutenu"
    if "prepar" in v or "cours" in v or "progress" in v:
        return "en_preparation"
    return "?"


def _annee(valeur) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(valeur or ""))
    return int(m.group(0)) if m else None


def _valider(n: Notice, type_defaut: str = "") -> Notice:
    n.titre = " ".join((n.titre or "").split())
    n.auteur = " ".join((n.auteur or "").split())
    if not n.type and type_defaut:
        n.type = type_defaut
    if not n.titre:
        n.erreurs.append("titre manquant")
    if not n.auteur:
        n.erreurs.append("auteur manquant")
    if n.type not in ("these", "memoire"):
        n.erreurs.append("type inconnu (thèse ou mémoire)" if n.type else "type manquant")
    if n.statut not in ("soutenu", "en_preparation"):
        n.erreurs.append("statut inconnu (soutenu ou en préparation)")
    if n.annee is None:
        n.erreurs.append("année manquante")
    if n.url and not re.match(r"^https?://", n.url, re.I):
        n.url = None
    for champ in ("directeur", "domaine", "faculte", "langue", "resume"):
        v = getattr(n, champ)
        setattr(n, champ, " ".join(v.split()) if isinstance(v, str) and v.strip() else None)
    if n.domaine_reesao and str(n.domaine_reesao).strip():
        from app.services import domaines
        code = domaines.depuis_metadonnee(str(n.domaine_reesao), {})
        if code:
            n.domaine_reesao = code
        else:
            n.erreurs.append("domaine REESAO inconnu (codes : " + ", ".join(domaines.DOMAINES) + ")")
    else:
        n.domaine_reesao = None
    n.mots_cles = [m for m in (" ".join(x.split()) for x in n.mots_cles) if m][:30]
    return n


# ── Lecture du fichier ────────────────────────────────────────────────

def decoder(contenu: bytes) -> str:
    if len(contenu) > TAILLE_MAX_MO * 1024 * 1024:
        raise ImportRefuse(f"Fichier trop lourd : {TAILLE_MAX_MO} Mo au plus.")
    if not contenu.strip():
        raise ImportRefuse("Le fichier est vide.")
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            return contenu.decode(encodage)
        except UnicodeDecodeError:
            continue
    raise ImportRefuse("Encodage non reconnu : enregistrez le fichier en UTF-8.")


def detecter_format(nom_fichier: str, texte: str) -> str:
    nom = (nom_fichier or "").lower()
    if nom.endswith(".ris") or re.search(r"^TY  - ", texte, re.M):
        return "ris"
    if nom.endswith((".bib", ".bibtex")) or re.search(r"^\s*@\w+\s*[{(]", texte, re.M):
        return "bibtex"
    if nom.endswith((".csv", ".txt", ".tsv")):
        return "csv"
    raise ImportRefuse("Format non reconnu : CSV, RIS ou BibTeX.")


def lire(nom_fichier: str, contenu: bytes, type_defaut: str = "") -> tuple[str, list[Notice]]:
    texte = decoder(contenu)
    fmt = detecter_format(nom_fichier, texte)
    notices = {"csv": lire_csv, "ris": lire_ris, "bibtex": lire_bibtex}[fmt](texte)
    if not notices:
        raise ImportRefuse("Aucune notice trouvée dans le fichier.")
    if len(notices) > LIGNES_MAX:
        raise ImportRefuse(f"Trop de notices ({len(notices)}) : {LIGNES_MAX} au plus par fichier.")
    notices = [_valider(n, type_defaut) for n in notices]
    # Doublons à l'intérieur du fichier : la première occurrence est gardée
    vues = {}
    for n in notices:
        if not n.valide:
            continue
        if n.cle in vues:
            n.erreurs.append(f"doublon de la ligne {vues[n.cle].ligne}")
        else:
            vues[n.cle] = n
    return fmt, notices


def lire_csv(texte: str) -> list[Notice]:
    echantillon = texte[:5000]
    try:
        dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t")
    except csv.Error:
        class dialecte(csv.excel):  # noqa: N801 — copie locale, pas la classe globale
            delimiter = ";" if echantillon.count(";") > echantillon.count(",") else ","
    lecteur = csv.reader(io.StringIO(texte), dialecte)
    lignes = list(lecteur)
    if not lignes:
        return []
    entetes = [ALIAS.get(_norm(h)) for h in lignes[0]]
    if "titre" not in entetes:
        raise ImportRefuse(
            "Colonne « titre » introuvable dans la première ligne. Téléchargez le modèle CSV."
        )
    notices = []
    for numero, valeurs in enumerate(lignes[1:], start=2):
        if not any(v.strip() for v in valeurs):
            continue
        d = {}
        for champ, valeur in zip(entetes, valeurs):
            if champ and valeur.strip():
                d[champ] = valeur.strip()
        notices.append(Notice(
            ligne=numero, titre=d.get("titre", ""), auteur=d.get("auteur", ""),
            type=_type(d.get("type", "")), statut=_statut(d.get("statut", "")),
            annee=_annee(d.get("annee")), directeur=d.get("directeur"),
            domaine=d.get("domaine"), faculte=d.get("faculte"), langue=d.get("langue"),
            resume=d.get("resume"), domaine_reesao=d.get("domaine_reesao"),
            mots_cles=re.split(r"\s*[;|]\s*", d["mots_cles"]) if d.get("mots_cles") else [],
            url=d.get("url"),
        ))
    return notices


def lire_ris(texte: str) -> list[Notice]:
    notices, courant, debut = [], None, 0
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", ligne)
        if not m:
            continue
        balise, valeur = m.group(1), m.group(2).strip()
        if balise == "TY":
            courant, debut = {"TY": valeur}, numero
        elif courant is None:
            continue
        elif balise == "ER":
            notices.append(_notice_ris(courant, debut))
            courant = None
        else:
            courant.setdefault(balise, []).append(valeur)
    if courant:
        notices.append(_notice_ris(courant, debut))
    return notices


def _notice_ris(r: dict, ligne: int) -> Notice:
    def un(*balises):
        for b in balises:
            if r.get(b):
                return r[b][0]
        return None
    ty = r.get("TY", "")
    genre = un("M3", "TT") or ""
    type_doc = _type(genre) if genre else ("these" if ty == "THES" else "")
    notes = " ".join(r.get("N1", []))
    return Notice(
        ligne=ligne, titre=un("TI", "T1") or "", auteur="; ".join(r.get("AU", r.get("A1", []))),
        type=type_doc, statut="en_preparation" if "prépar" in notes.lower() else "soutenu",
        annee=_annee(un("PY", "Y1", "DA")), directeur="; ".join(r.get("A2", []) + r.get("A3", [])) or None,
        domaine=un("C1"), faculte=un("AD"), langue=un("LA"),
        resume=un("AB", "N2"), mots_cles=r.get("KW", []), url=un("UR", "L2"),
    )


def lire_bibtex(texte: str) -> list[Notice]:
    notices = []
    for m in re.finditer(r"@(\w+)\s*[{(]", texte):
        genre = m.group(1).lower()
        if genre in ("comment", "string", "preamble"):
            continue
        corps, fin = _bloc(texte, m.end())
        if corps is None:
            continue
        ligne = texte.count("\n", 0, m.start()) + 1
        champs = _champs_bibtex(corps)
        type_doc = {"phdthesis": "these", "mastersthesis": "memoire"}.get(genre) \
            or _type(champs.get("type", ""))
        notices.append(Notice(
            ligne=ligne, titre=champs.get("title", ""),
            auteur=champs.get("author", "").replace(" and ", "; "),
            type=type_doc, statut=_statut(champs.get("statut", "") or
                                          ("en préparation" if "prépar" in champs.get("note", "").lower() else "")),
            annee=_annee(champs.get("year") or champs.get("date")),
            directeur=(champs.get("supervisor") or champs.get("advisor") or "").replace(" and ", "; ") or None,
            domaine=champs.get("field") or champs.get("domaine"),
            faculte=champs.get("institution") or champs.get("department"),
            langue=champs.get("language") or champs.get("langid"),
            resume=champs.get("abstract"),
            mots_cles=re.split(r"\s*[,;]\s*", champs["keywords"]) if champs.get("keywords") else [],
            url=champs.get("url"),
        ))
    return notices


def _bloc(texte: str, debut: int):
    """Contenu d'une entrée BibTeX jusqu'à l'accolade fermante appariée."""
    profondeur = 1
    for i in range(debut, len(texte)):
        c = texte[i]
        if c in "{(":
            profondeur += 1
        elif c in "})":
            profondeur -= 1
            if profondeur == 0:
                return texte[debut:i], i
    return None, len(texte)


def _champs_bibtex(corps: str) -> dict:
    champs, i, n = {}, 0, len(corps)
    virgule = corps.find(",")          # saute la clé de citation
    i = virgule + 1 if virgule >= 0 else 0
    while i < n:
        m = re.compile(r"\s*([\w-]+)\s*=\s*").match(corps, i)
        if not m:
            i += 1
            continue
        nom, i = m.group(1).lower(), m.end()
        if i < n and corps[i] == "{":
            valeur, fin = _bloc(corps, i + 1)
            i = fin + 1
        elif i < n and corps[i] == '"':
            fin = corps.find('"', i + 1)
            valeur, i = corps[i + 1:fin], fin + 1
        else:
            m2 = re.compile(r"[^,]*").match(corps, i)
            valeur, i = m2.group(0), m2.end()
        champs[nom] = re.sub(r"[{}]", "", " ".join((valeur or "").split()))
        virgule = corps.find(",", i)
        i = virgule + 1 if virgule >= 0 else n
    return champs


def modele_csv() -> str:
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";")
    ecrivain.writerow(COLONNES_CSV)
    ecrivain.writerow([
        "La gouvernance locale au Sénégal", "NDIAYE Aminata", "thèse", "soutenu", "2021",
        "FALL Moussa", "SJPA", "Sciences politiques", "École doctorale ETHOS", "français",
        "Résumé du travail…", "décentralisation; collectivités", "https://depot.exemple.sn/123",
    ])
    ecrivain.writerow([
        "Étude des sols de la vallée", "SOW Awa", "mémoire", "en préparation", "2025",
        "", "SA", "Agronomie", "UFR S2ATA", "français", "", "sols; irrigation", "",
    ])
    return "﻿" + tampon.getvalue()
