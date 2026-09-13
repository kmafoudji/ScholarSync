"""
Rattrapage de schéma pour les installations existantes.

`Base.metadata.create_all` crée les tables manquantes mais n'ajoute jamais une
colonne à une table déjà présente. Les colonnes introduites après une mise en
production sont donc déclarées ici et appliquées au démarrage, de façon
idempotente.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# (table, colonne, définition SQL)
COLONNES = [
    ("sync_logs", "documents_total", "INTEGER DEFAULT 0"),
    ("sync_logs", "documents_traites", "INTEGER DEFAULT 0"),
]


def ensure_schema(engine) -> None:
    with engine.begin() as conn:
        for table, colonne, definition in COLONNES:
            try:
                conn.execute(text(
                    f'ALTER TABLE {table} '
                    f'ADD COLUMN IF NOT EXISTS {colonne} {definition}'
                ))
            except Exception:
                # Une base non encore initialisée ou un droit manquant ne doit
                # pas empêcher le démarrage : create_all a déjà fait le gros.
                logger.warning(
                    "Colonne %s.%s non appliquée", table, colonne, exc_info=True
                )
