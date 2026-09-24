"""
Qui peut faire quoi dans l'administration.

Trois rôles :

- super_admin          — tout, sur tout le catalogue national ;
- admin_etablissement  — son établissement uniquement : ses documents,
                         la synchronisation de sa source Zotero, sa fiche
                         (nom, site, logo), ses exports ;
- lecteur              — consultation seule, dans le même périmètre.

La règle est une LISTE BLANCHE : une route d'administration non listée
ici est réservée au super administrateur. Une route ajoutée demain est
donc fermée par défaut — l'oubli d'un contrôle ne peut plus ouvrir une
page de paramétrage à tous les établissements, comme c'était le cas
jusqu'ici (seules 7 routes sur 60 vérifiaient le rôle).

La liste blanche dit quelles pages sont accessibles ; le périmètre
(`perimetre`) dit quelles données elles montrent. Les deux sont
nécessaires : sans le second, « Synchronisation » serait ouverte à
l'établissement mais lui laisserait lancer celle d'un autre.
"""
from __future__ import annotations

import re

SUPER_ADMIN = "super_admin"
ADMIN_ETABLISSEMENT = "admin_etablissement"
LECTEUR = "lecteur"

# Ancien nom du rôle d'établissement (schéma SQL initial). Les comptes
# qui le portent encore sont traités comme admin_etablissement.
ALIAS_ROLES = {"gestionnaire_bu": ADMIN_ETABLISSEMENT}

_UUID = r"[0-9a-fA-F-]{36}"

# (méthodes, chemin) ouverts aux rôles d'établissement
ROUTES_ETABLISSEMENT = [
    ({"GET"}, r"/admin"),
    ({"GET"}, r"/admin/documents"),
    ({"POST"}, rf"/admin/documents/{_UUID}/toggle-acces"),
    ({"GET"}, r"/admin/sync"),
    ({"GET"}, r"/admin/sync/etat"),
    ({"GET"}, r"/admin/sync-logs"),
    ({"POST"}, r"/admin/sync/lancer/\d+"),
    ({"GET"}, r"/admin/exports"),
    ({"GET"}, r"/admin/exports/documents"),
    ({"GET"}, r"/admin/exports/rapport"),
    ({"GET"}, r"/admin/mon-etablissement"),
    ({"POST"}, r"/admin/mon-etablissement"),
]
_COMPILEES = [(m, re.compile(rf"^{motif}/?$")) for m, motif in ROUTES_ETABLISSEMENT]

# Routes d'administration accessibles sans être connecté
ROUTES_PUBLIQUES = {
    "/admin/connexion", "/admin/setup",
    "/admin/mot-de-passe-oublie", "/admin/reset-password",
    "/admin/deconnexion",
}

# Valeur de filtre qui ne correspond à aucun établissement : un compte
# d'établissement sans établissement rattaché ne doit rien voir, surtout
# pas tout (un filtre « None » reviendrait à ne pas filtrer).
AUCUN = "\x00aucun"


def role_de(utilisateur) -> str:
    role = getattr(utilisateur, "role", None) or ""
    return ALIAS_ROLES.get(role, role)


def est_super_admin(utilisateur) -> bool:
    return role_de(utilisateur) == SUPER_ADMIN


def peut_modifier(utilisateur) -> bool:
    return role_de(utilisateur) in (SUPER_ADMIN, ADMIN_ETABLISSEMENT)


def perimetre(utilisateur):
    """Code d'établissement auquel limiter les données, ou None pour tout.

    Ne renvoie jamais None pour un compte qui n'est pas super admin.
    """
    if est_super_admin(utilisateur):
        return None
    return getattr(utilisateur, "etablissement_code", None) or AUCUN


def autorise(utilisateur, methode: str, chemin: str) -> bool:
    """La page `chemin` est-elle ouverte à cet utilisateur ?"""
    role = role_de(utilisateur)
    if role == SUPER_ADMIN:
        return True
    if role not in (ADMIN_ETABLISSEMENT, LECTEUR):
        return False
    methode = methode.upper()
    if methode == "HEAD":
        methode = "GET"
    if role == LECTEUR and methode != "GET":
        return False
    return any(methode in m and motif.match(chemin) for m, motif in _COMPILEES)
