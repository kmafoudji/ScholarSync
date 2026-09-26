"""Référentiel des établissements (app/services/referentiel.py).

    cd backend && python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import numerotation as num  # noqa: E402
from app.services import referentiel as ref  # noqa: E402


def test_codes_uniques_et_valides():
    codes = [c for _, _, _, c in ref.ETABLISSEMENTS]
    sigles = [s for s, _, _, _ in ref.ETABLISSEMENTS]
    assert len(set(codes)) == len(codes)
    assert len(set(sigles)) == len(sigles)
    for c in codes:
        assert num.MOTIF_CODE_NUMERO.match(c), c


def test_codes_suivent_la_regle_des_familles():
    familles = {lettre for lettre, _, _ in ref.FAMILLES}
    for sigle, _, _, code in ref.ETABLISSEMENTS:
        assert code[0] in familles, sigle
    assert all(code[0] == "U" for s, _, _, code in ref.ETABLISSEMENTS if s.startswith("U"))


def test_valeurs_par_defaut_alignees():
    for sigle, _, _, code in ref.ETABLISSEMENTS:
        assert num.code_par_defaut(sigle) == code, sigle


def test_recodages_vers_le_referentiel():
    cibles = {s: c for s, _, _, c in ref.ETABLISSEMENTS}
    for sigle, _, nouveau in ref.RECODAGES:
        assert cibles[sigle] == nouveau
    # UZ libère US avant que l'USSEIN ne le prenne
    assert ref.RECODAGES[0] == ("UASZ", "US", "UZ")


def test_suite_des_codes_prives():
    assert ref.SUITE_PRIVES[0] == "P1" and ref.SUITE_PRIVES[8] == "P9"
    assert ref.SUITE_PRIVES[9] == "PA" and ref.SUITE_PRIVES[-1] == "PZ"
    assert len(ref.SUITE_PRIVES) == 35
    assert all(num.MOTIF_CODE_NUMERO.match(c) for c in ref.SUITE_PRIVES)
