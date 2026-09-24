"""Rapports PDF : chemins de logos et rendu (app/services/rapports.py).

    cd backend && python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rapports import chemin_local  # noqa: E402


def test_chemin_local():
    assert chemin_local(None) is None
    assert chemin_local("/static/css/main.css?v=12") == "static/css/main.css"
    assert chemin_local("/static/../core/config.py") is None
    assert chemin_local("https://ailleurs.example/logo.png") is None
    assert chemin_local("/static/img/uploads/absent.png") is None


def test_weasyprint_produit_un_pdf():
    """Garde-fou de dépendances : WeasyPrint 62 plante avec pydyf ≥ 0.11
    (« 'super' object has no attribute 'transform' »)."""
    from weasyprint import HTML
    assert HTML(string="<p>Rapport</p>").write_pdf().startswith(b"%PDF")
