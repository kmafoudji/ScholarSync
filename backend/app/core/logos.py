"""
Contrôle de qualité et de sûreté des logos téléversés.

Deux exigences, vérifiées ici plutôt que dans chaque route :

- Sûreté. Le fichier est servi depuis l'origine de l'application : un SVG
  qui contient du script s'exécuterait dans la session d'un visiteur, et
  une image « polyglotte » peut cacher autre chose derrière un en-tête
  valide. Les images matricielles sont donc entièrement décodées puis
  réencodées — seuls les pixels survivent, sans métadonnées (EXIF, GPS,
  logiciel) ni octets parasites. Les SVG sont analysés élément par élément.

- Qualité. Un logo trop petit devient flou sur un écran haute densité ;
  un logo perdu au milieu d'un grand fond transparent paraît minuscule
  une fois affiché ; un bandeau très allongé devient illisible dans le
  carré d'une liste. On refuse ces cas avec un message qui dit quoi
  corriger, au lieu de publier un logo médiocre.

Ce module ne touche ni à la base ni au disque : il reçoit des octets et
rend des octets, ce qui le rend testable seul (tests/test_logos.py).
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from PIL import Image, ImageOps, UnidentifiedImageError

# Au-delà, le décodage seul peut saturer la mémoire (bombe de
# décompression : un PNG de quelques Ko qui se déploie en gigaoctets).
PIXELS_MAX = 40_000_000


@dataclass(frozen=True)
class Profil:
    """Exigences d'un emplacement de logo."""
    libelle: str
    min_cote_court: int     # px
    min_cote_long: int      # px
    ratio_max: float        # côté long / côté court
    cote_long_sortie: int   # l'image est réduite au-delà (poids de page)
    conseil: str


PROFILS = {
    # Logo de l'outil : bandeau d'en-tête, affiché sur 32 px de haut.
    "outil": Profil(
        libelle="logo de l'outil",
        min_cote_court=60, min_cote_long=200, ratio_max=8.0,
        cote_long_sortie=1200,
        conseil="un logo horizontal d'au moins 200 × 60 px, fond transparent",
    ),
    # Logo d'établissement : vignette carrée dans les listes et les fiches.
    "etablissement": Profil(
        libelle="logo d'établissement",
        min_cote_court=150, min_cote_long=150, ratio_max=3.0,
        cote_long_sortie=800,
        conseil="un emblème plutôt carré d'au moins 300 × 300 px, fond transparent",
    ),
}

TYPES_ACCEPTES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}

# Format réellement décodé par Pillow → format déclaré attendu
FORMATS_PILLOW = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


class LogoRefuse(ValueError):
    """Le fichier ne peut pas être publié ; le message dit pourquoi."""


@dataclass
class LogoValide:
    contenu: bytes
    extension: str                 # extension du fichier à écrire
    largeur: int | None = None     # None pour un SVG sans dimensions
    hauteur: int | None = None
    avertissements: list[str] = field(default_factory=list)


def controler_logo(contenu: bytes, type_mime: str, profil: str = "etablissement",
                   taille_max_mo: float = 2) -> LogoValide:
    """Valide un logo et renvoie la version à enregistrer.

    Lève LogoRefuse avec un message en français, destiné à l'utilisateur.
    """
    p = PROFILS[profil]
    extension = TYPES_ACCEPTES.get((type_mime or "").lower().strip())
    if not extension:
        raise LogoRefuse("Format non accepté. Formats possibles : PNG, JPEG, WebP ou SVG.")
    if not contenu:
        raise LogoRefuse("Le fichier est vide.")
    if len(contenu) > taille_max_mo * 1024 * 1024:
        raise LogoRefuse(
            f"Le fichier pèse {len(contenu) / 1024 / 1024:.1f} Mo, "
            f"au-delà de la limite de {taille_max_mo:g} Mo."
        )
    if extension == "svg":
        return _controler_svg(contenu, p)
    return _controler_matriciel(contenu, extension, p)


# ── Images matricielles ──────────────────────────────────────────────

def _controler_matriciel(contenu: bytes, extension: str, p: Profil) -> LogoValide:
    avertissements: list[str] = []

    try:
        with Image.open(io.BytesIO(contenu)) as sonde:
            format_reel = FORMATS_PILLOW.get(sonde.format or "")
            largeur, hauteur = sonde.size
            sonde.verify()  # détecte un fichier tronqué ou corrompu
    except Image.DecompressionBombError:
        raise LogoRefuse("Image démesurée : réduisez-la avant de la téléverser.")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise LogoRefuse(
            f"Le fichier n'est pas une image {extension.upper()} lisible "
            "(fichier corrompu, ou extension trompeuse)."
        )

    if format_reel is None:
        raise LogoRefuse("Format d'image non pris en charge : utilisez PNG, JPEG ou WebP.")
    if format_reel != extension:
        raise LogoRefuse(
            f"Le fichier se présente comme un {extension.upper()} mais contient "
            f"un {format_reel.upper()}. Réexportez-le depuis votre logiciel."
        )
    if largeur * hauteur > PIXELS_MAX:
        raise LogoRefuse(
            f"Image de {largeur} × {hauteur} px : trop grande. "
            f"Réduisez-la à {p.cote_long_sortie} px de large environ."
        )

    # verify() laisse l'image inutilisable : on la rouvre pour la décoder.
    with Image.open(io.BytesIO(contenu)) as brute:
        if getattr(brute, "n_frames", 1) > 1:
            raise LogoRefuse("Les images animées ne sont pas acceptées comme logo.")
        image = ImageOps.exif_transpose(brute)  # photo prise de travers
        image.load()

    a_transparence = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if a_transparence else "RGB")

    # Marges transparentes : un logo de 80 px au centre d'une toile de
    # 1000 px passe la mesure des dimensions, mais s'affiche minuscule.
    if a_transparence:
        cadre = image.getchannel("A").getbbox()
        if cadre is None:
            raise LogoRefuse("L'image est entièrement transparente.")
        if cadre != (0, 0, image.width, image.height):
            image = image.crop(cadre)
            avertissements.append("Les marges transparentes ont été retirées.")

    _refuser_si_uniforme(image)

    l, h = image.size
    court, long_ = min(l, h), max(l, h)
    if court < p.min_cote_court or long_ < p.min_cote_long:
        detail = " (marges transparentes retirées)" if avertissements else ""
        raise LogoRefuse(
            f"Image trop petite : {l} × {h} px{detail}. Il faut au moins "
            f"{p.min_cote_court} px sur le petit côté et {p.min_cote_long} px "
            f"sur le grand, sinon le {p.libelle} sera flou. Conseil : {p.conseil}."
        )
    if long_ / court > p.ratio_max:
        raise LogoRefuse(
            f"Proportions trop allongées ({l} × {h} px, soit {long_ / court:.1f}:1 ; "
            f"maximum {p.ratio_max:g}:1). Conseil : {p.conseil}."
        )

    if not a_transparence:
        avertissements.append(
            "Ce logo n'a pas de fond transparent : un PNG ou un SVG transparent "
            "s'intégrera mieux."
        )

    if long_ > p.cote_long_sortie:
        image.thumbnail((p.cote_long_sortie, p.cote_long_sortie), Image.LANCZOS)

    sortie = io.BytesIO()
    image.save(sortie, format="PNG", optimize=True)
    return LogoValide(
        contenu=sortie.getvalue(), extension="png",
        largeur=image.width, hauteur=image.height,
        avertissements=avertissements,
    )


def _refuser_si_uniforme(image: Image.Image) -> None:
    """Une image d'une seule couleur n'est pas un logo (export raté)."""
    vignette = image.copy()
    vignette.thumbnail((128, 128))
    if vignette.mode == "RGBA":
        # Seuls les pixels visibles comptent.
        fond = Image.new("RGBA", vignette.size, (0, 0, 0, 0))
        visibles = Image.composite(vignette, fond, vignette.getchannel("A"))
        extremes = visibles.convert("RGB").getextrema()
        alpha = vignette.getchannel("A").getextrema()
        if alpha[0] == alpha[1] and all(lo == hi for lo, hi in extremes):
            raise LogoRefuse("L'image est d'une seule couleur : ce n'est pas un logo.")
        return
    if all(lo == hi for lo, hi in vignette.getextrema()):
        raise LogoRefuse("L'image est d'une seule couleur : ce n'est pas un logo.")


# ── SVG ──────────────────────────────────────────────────────────────

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}href"

# Éléments qui exécutent du code, chargent un document ou animent des
# attributs (une animation peut réécrire un href en « javascript: »).
ELEMENTS_INTERDITS = {
    "script", "foreignobject", "iframe", "embed", "object", "handler",
    "listener", "animate", "animatemotion", "animatetransform", "set",
    "discard",
}
ELEMENTS_DESSIN = {
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "image", "use",
}
MOTIF_LONGUEUR = re.compile(r"^\s*([0-9.]+)\s*(px)?\s*$")


def _nom_local(balise: str) -> str:
    return balise.rsplit("}", 1)[-1].lower() if isinstance(balise, str) else ""


def _controler_svg(contenu: bytes, p: Profil) -> LogoValide:
    try:
        texte = contenu.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LogoRefuse("Ce SVG n'est pas encodé en UTF-8 : réexportez-le.")

    bas = texte.lower()
    # DOCTYPE / ENTITY : entités externes (lecture de fichiers du serveur)
    # et « billion laughs » (expansion exponentielle). Un logo n'en a
    # jamais besoin.
    if "<!doctype" in bas or "<!entity" in bas:
        raise LogoRefuse("Ce SVG contient une déclaration DOCTYPE ou ENTITY : il a été refusé.")
    if "javascript:" in bas or "vbscript:" in bas:
        raise LogoRefuse("Ce SVG contient du script : il a été refusé.")

    try:
        racine = ET.fromstring(texte)
    except ET.ParseError:
        raise LogoRefuse("Ce SVG est mal formé : ouvrez-le et réexportez-le.")
    if _nom_local(racine.tag) != "svg":
        raise LogoRefuse("Le fichier n'est pas un SVG.")

    nb_dessins = 0
    for el in racine.iter():
        nom = _nom_local(el.tag)
        if nom in ELEMENTS_INTERDITS:
            raise LogoRefuse(f"Ce SVG contient un élément <{nom}> interdit : il a été refusé.")
        if nom in ELEMENTS_DESSIN:
            nb_dessins += 1
        for attr, valeur in el.attrib.items():
            a = _nom_local(attr)
            if a.startswith("on"):
                raise LogoRefuse("Ce SVG contient du script (attribut on…) : il a été refusé.")
            if a == "href" or attr == XLINK:
                v = valeur.strip().lower()
                if not (v.startswith("#") or re.match(r"data:image/(png|jpeg|webp);", v)):
                    raise LogoRefuse(
                        "Ce SVG charge une ressource externe : intégrez-la au fichier "
                        "ou vectorisez-la."
                    )
            if a == "style" and ("url(" in valeur.lower() and "url(#" not in valeur.lower()):
                raise LogoRefuse("Ce SVG charge une ressource externe via un style.")
        if nom == "style" and el.text and ("@import" in el.text or "url(http" in el.text.lower()):
            raise LogoRefuse("Ce SVG importe une feuille de style externe.")

    if nb_dessins == 0:
        raise LogoRefuse("Ce SVG ne contient aucun dessin.")

    largeur, hauteur = _dimensions_svg(racine)
    avertissements: list[str] = []
    if largeur is None:
        raise LogoRefuse(
            "Ce SVG n'a ni viewBox ni largeur/hauteur : il ne peut pas être mis à "
            "l'échelle correctement. Ajoutez un viewBox à l'export."
        )
    if racine.get("viewBox") is None:
        avertissements.append(
            "Ce SVG n'a pas de viewBox : il pourrait mal se redimensionner."
        )
    court, long_ = min(largeur, hauteur), max(largeur, hauteur)
    if court <= 0:
        raise LogoRefuse("Les dimensions de ce SVG sont nulles.")
    if long_ / court > p.ratio_max:
        raise LogoRefuse(
            f"Proportions trop allongées ({long_ / court:.1f}:1 ; maximum "
            f"{p.ratio_max:g}:1). Conseil : {p.conseil}."
        )

    return LogoValide(
        contenu=contenu, extension="svg",
        largeur=round(largeur), hauteur=round(hauteur),
        avertissements=avertissements,
    )


def _dimensions_svg(racine) -> tuple[float | None, float | None]:
    vb = racine.get("viewBox")
    if vb:
        morceaux = re.split(r"[\s,]+", vb.strip())
        if len(morceaux) == 4:
            try:
                return float(morceaux[2]), float(morceaux[3])
            except ValueError:
                pass
    l, h = racine.get("width", ""), racine.get("height", "")
    ml, mh = MOTIF_LONGUEUR.match(l), MOTIF_LONGUEUR.match(h)
    if ml and mh:
        return float(ml.group(1)), float(mh.group(1))
    return None, None
