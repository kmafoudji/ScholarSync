"""
Initialisation et rattrapage du schéma.

`Base.metadata.create_all` crée les tables manquantes mais n'ajoute jamais
une colonne à une table déjà présente. Les colonnes introduites après une
mise en production sont déclarées ici et appliquées au démarrage, de façon
idempotente.

Ces opérations s'exécutaient auparavant à l'import du module applicatif,
sans gestion d'erreur : une base momentanément injoignable — le temps que
PostgreSQL finisse de démarrer, ou un mot de passe désaccordé après une
rotation de secrets — faisait échouer l'import, mourir le worker gunicorn,
et le reverse proxy renvoyait un 502 sans explication.

Le démarrage est donc devenu tolérant : on réessaie, puis on laisse
l'application démarrer en état dégradé. `/sante` dit alors précisément ce
qui ne va pas, ce qu'un processus mort ne peut pas faire.
"""
import logging
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# (table, colonne, définition SQL)
COLONNES = [
    ("sync_logs", "documents_total", "INTEGER DEFAULT 0"),
    ("sync_logs", "documents_traites", "INTEGER DEFAULT 0"),
]

# Corrections de données et de contraintes, idempotentes.
#
# Rôles : le schéma SQL initial n'autorisait que 'super_admin' et
# 'gestionnaire_bu', alors que l'application crée des comptes
# 'admin_etablissement' et 'lecteur'. Sur une base initialisée par ce
# fichier, créer un compte d'établissement échouait (« Création
# impossible »). On retire l'ancienne contrainte et on renomme l'ancien
# rôle ; la liste des rôles valides est contrôlée par l'application.
RATTRAPAGES = [
    "ALTER TABLE utilisateurs DROP CONSTRAINT IF EXISTS utilisateurs_role_check",
    "UPDATE utilisateurs SET role = 'admin_etablissement' WHERE role = 'gestionnaire_bu'",
]

# État partagé, lu par la sonde /sante
etat = {"pret": False, "erreur": None}


def ensure_schema(engine) -> None:
    """Applique les colonnes ajoutées après coup."""
    with engine.begin() as conn:
        for table, colonne, definition in COLONNES:
            try:
                conn.execute(text(
                    f'ALTER TABLE {table} '
                    f'ADD COLUMN IF NOT EXISTS {colonne} {definition}'
                ))
            except SQLAlchemyError:
                logger.warning(
                    "Colonne %s.%s non appliquée", table, colonne, exc_info=True
                )
    # Une transaction par correction : l'échec de l'une ne doit pas
    # annuler les autres.
    for requete in RATTRAPAGES:
        try:
            with engine.begin() as conn:
                conn.execute(text(requete))
        except SQLAlchemyError:
            logger.warning("Rattrapage non appliqué : %s", requete, exc_info=True)


def initialiser(engine, base, tentatives: int = 10, delai: float = 3.0) -> bool:
    """
    Crée les tables puis applique les colonnes de rattrapage.

    Réessaie tant que la base n'est pas joignable — au premier démarrage,
    PostgreSQL peut mettre plusieurs secondes à accepter les connexions.
    Ne lève jamais : renvoie True si le schéma est prêt, False sinon, et
    laisse l'appelant décider. Une erreur d'authentification n'est pas
    réessayée : attendre ne la résoudra pas.
    """
    derniere = None

    for essai in range(1, tentatives + 1):
        try:
            base.metadata.create_all(bind=engine)
            ensure_schema(engine)
            etat["pret"] = True
            etat["erreur"] = None
            if essai > 1:
                logger.info("Schéma prêt après %s tentative(s)", essai)
            return True

        except SQLAlchemyError as e:
            derniere = e
            message = str(e)

            if "password authentication failed" in message or "role" in message and "does not exist" in message:
                # Identifiants refusés : réessayer ne changera rien.
                logger.error(
                    "Authentification PostgreSQL refusée. Le mot de passe de "
                    ".env ne correspond pas à celui enregistré dans la base. "
                    "Après une rotation de secrets sur une base existante, il "
                    "faut changer le mot de passe des deux côtés — voir "
                    "docs/deploiement.md."
                )
                break

            if essai < tentatives:
                logger.warning(
                    "Base injoignable (tentative %s/%s), nouvel essai dans %.0f s",
                    essai, tentatives, delai,
                )
                time.sleep(delai)

    etat["pret"] = False
    etat["erreur"] = str(derniere)[:300] if derniere else "cause inconnue"
    logger.error(
        "Schéma non initialisé : l'application démarre en état dégradé. "
        "Interrogez /sante pour le détail."
    )
    return False
