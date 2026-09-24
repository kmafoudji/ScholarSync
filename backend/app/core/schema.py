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
    ("utilisateurs", "mdp_modifie_le", "TIMESTAMPTZ"),
    ("sync_logs", "documents_supprimes", "INTEGER DEFAULT 0"),
    ("etablissements", "code_numero", "VARCHAR(2)"),
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
    # Numéro national : attribué à la soutenance seulement. Les travaux en
    # préparation déjà numérotés (lettre P en 6e position) perdent leur
    # numéro ; ils en recevront un, avec la lettre S, une fois soutenus.
    # Idempotent : plus aucun numéro en P n'est produit.
    "ALTER TABLE documents ALTER COLUMN numero_national DROP NOT NULL",
    "UPDATE documents SET numero_national = NULL WHERE statut = 'en_preparation' "
    "AND substring(numero_national from 6 for 1) = 'P'",
    # Codes de numérotation : repris de l'ancienne table écrite dans le
    # code, sinon les deux premiers caractères du code — exactement ce
    # que produisait l'ancien calcul, pour que les numéros existants
    # restent cohérents.
    "UPDATE etablissements SET code_numero = CASE code "
    "WHEN 'UCAD' THEN 'UC' WHEN 'UGB' THEN 'UG' WHEN 'UADB' THEN 'UA' "
    "WHEN 'UASZ' THEN 'US' WHEN 'UIDT' THEN 'UI' WHEN 'UNCHK' THEN 'UN' "
    "ELSE upper(substring(code from 1 for 2)) END WHERE code_numero IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS etablissements_code_numero_key "
    "ON etablissements (code_numero)",
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
