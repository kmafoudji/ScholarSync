"""Contrôle de qualité et de sûreté des logos (app/core/logos.py).

    cd backend && python -m pytest tests/
"""
import io
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logos import LogoRefuse, controler_logo  # noqa: E402


def _image(l, h, mode="RGBA", fond=(0, 0, 0, 0), dessin=(26, 58, 92, 255),
           marge=0, format="PNG", **options):
    img = Image.new(mode, (l, h), fond if mode == "RGBA" else fond[:3])
    # Un motif à deux couleurs, pour ne pas être « uniforme »
    for x in range(marge, l - marge):
        for y in range(marge, h - marge):
            if (x // 7 + y // 7) % 2 == 0:
                img.putpixel((x, y), dessin if mode == "RGBA" else dessin[:3])
            elif mode == "RGB":
                img.putpixel((x, y), (200, 30, 40))
            else:
                img.putpixel((x, y), (200, 30, 40, 255))
    sortie = io.BytesIO()
    img.save(sortie, format=format, **options)
    return sortie.getvalue()


def _ouvrir(contenu):
    return Image.open(io.BytesIO(contenu))


# ── Acceptés ──────────────────────────────────────────────────────────

def test_png_transparent_carre_accepte_et_reencode():
    r = controler_logo(_image(400, 400), "image/png")
    assert r.extension == "png" and (r.largeur, r.hauteur) == (400, 400)
    assert r.avertissements == []


def test_jpeg_accepte_avec_avertissement_de_fond():
    r = controler_logo(_image(400, 400, mode="RGB", format="JPEG", quality=95), "image/jpeg")
    assert r.extension == "png"
    assert any("transparent" in a for a in r.avertissements)


def test_grand_logo_reduit():
    r = controler_logo(_image(2000, 1500), "image/png")
    assert max(r.largeur, r.hauteur) == 800  # profil établissement


def test_metadonnees_retirees():
    img = Image.new("RGB", (400, 400), (10, 10, 10))
    img.putpixel((5, 5), (250, 250, 250))
    exif = Image.Exif()
    exif[0x0131] = "Logiciel secret"  # Software
    sortie = io.BytesIO()
    img.save(sortie, format="JPEG", exif=exif)
    r = controler_logo(sortie.getvalue(), "image/jpeg")
    assert b"Logiciel secret" not in r.contenu
    assert not _ouvrir(r.contenu).getexif()


def test_marges_transparentes_retirees():
    r = controler_logo(_image(600, 600, marge=100), "image/png")
    assert (r.largeur, r.hauteur) == (400, 400)
    assert any("marges" in a for a in r.avertissements)


def test_svg_propre_accepte():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           b'<circle cx="50" cy="50" r="40" fill="#1a3a5c"/></svg>')
    r = controler_logo(svg, "image/svg+xml")
    assert r.extension == "svg" and r.contenu == svg


def test_profil_outil_accepte_un_bandeau():
    r = controler_logo(_image(600, 120), "image/png", profil="outil")
    assert (r.largeur, r.hauteur) == (600, 120)


# ── Refusés : qualité ────────────────────────────────────────────────

def test_trop_petit_refuse():
    with pytest.raises(LogoRefuse, match="trop petite"):
        controler_logo(_image(120, 120), "image/png")


def test_petit_logo_dans_grande_toile_refuse():
    """800 × 800 px, mais le dessin n'en occupe que 100 × 100."""
    with pytest.raises(LogoRefuse, match="marges transparentes"):
        controler_logo(_image(800, 800, marge=350), "image/png")


def test_trop_allonge_pour_une_vignette():
    with pytest.raises(LogoRefuse, match="allongées"):
        controler_logo(_image(1000, 200), "image/png")


def test_image_uniforme_refusee():
    sortie = io.BytesIO()
    Image.new("RGB", (400, 400), (255, 255, 255)).save(sortie, format="PNG")
    with pytest.raises(LogoRefuse, match="seule couleur"):
        controler_logo(sortie.getvalue(), "image/png")


def test_image_entierement_transparente_refusee():
    sortie = io.BytesIO()
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(sortie, format="PNG")
    with pytest.raises(LogoRefuse, match="transparente"):
        controler_logo(sortie.getvalue(), "image/png")


def test_trop_lourd_refuse():
    with pytest.raises(LogoRefuse, match="Mo"):
        controler_logo(b"\x89PNG" + b"0" * (3 * 1024 * 1024), "image/png")


# ── Refusés : sûreté ─────────────────────────────────────────────────

def test_type_trompeur_refuse():
    jpeg = _image(400, 400, mode="RGB", format="JPEG")
    with pytest.raises(LogoRefuse, match="contient"):
        controler_logo(jpeg, "image/png")


def test_fichier_corrompu_refuse():
    png = _image(400, 400)
    with pytest.raises(LogoRefuse):
        controler_logo(png[: len(png) // 3], "image/png")


def test_html_deguise_refuse():
    with pytest.raises(LogoRefuse):
        controler_logo(b"<html><script>alert(1)</script></html>", "image/png")


def test_format_non_accepte():
    with pytest.raises(LogoRefuse, match="Format"):
        controler_logo(b"GIF89a....", "image/gif")


@pytest.mark.parametrize("svg", [
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="5" height="5"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="alert(1)"><rect width="5" height="5"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><a href="javascript:alert(1)"><rect width="5" height="5"/></a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><foreignObject><div/></foreignObject><rect width="5" height="5"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10"><image xlink:href="https://pistage.example/p.png" width="5" height="5"/></svg>',
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>&x;</text></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><set attributeName="href" to="javascript:alert(1)"/><rect width="5" height="5"/></svg>',
])
def test_svg_dangereux_refuse(svg):
    with pytest.raises(LogoRefuse):
        controler_logo(svg, "image/svg+xml")


def test_svg_sans_dimensions_refuse():
    with pytest.raises(LogoRefuse, match="viewBox"):
        controler_logo(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>',
                       "image/svg+xml")


def test_svg_vide_refuse():
    with pytest.raises(LogoRefuse, match="aucun dessin"):
        controler_logo(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>',
                       "image/svg+xml")
