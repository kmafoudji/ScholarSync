"""
ScholarSync — point d'entrée avec planificateur de synchronisation Zotero.
"""
import logging

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import tasks
from app.core.database import SessionLocal
from app.main import app
from app.models import Parametre

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("scholarsync")

scheduler = AsyncIOScheduler()

SYNC_JOB_ID = "sync_zotero"


def _intervalle_minutes() -> int:
    db = SessionLocal()
    try:
        row = (
            db.query(Parametre)
            .filter(Parametre.cle == "sync_intervalle_min")
            .first()
        )
        return max(5, int(row.valeur)) if row and row.valeur else 60
    except (TypeError, ValueError):
        return 60
    finally:
        db.close()


def lancer_sync() -> None:
    """
    Déclenche la synchronisation planifiée.

    sync_all est bloquante (appels HTTP Zotero) : elle part dans un thread
    avec sa propre session, sinon elle figerait la boucle d'événements
    pendant toute la durée de la sync.
    """
    from app.sync.engine import sync_all

    if not tasks.run_in_background(sync_all, declenchement="auto", key="sync"):
        logger.info("Sync planifiée ignorée : une synchronisation est déjà en cours")


@app.on_event("startup")
async def startup():
    minutes = _intervalle_minutes()
    scheduler.add_job(
        lancer_sync,
        trigger=IntervalTrigger(minutes=minutes),
        id=SYNC_JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Planificateur démarré — synchronisation toutes les %s minutes", minutes)


@app.on_event("shutdown")
async def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def reprogrammer_sync(minutes: int) -> None:
    """Appelé quand l'administrateur change l'intervalle depuis l'interface."""
    if scheduler.running:
        scheduler.reschedule_job(
            SYNC_JOB_ID, trigger=IntervalTrigger(minutes=max(5, minutes))
        )
        logger.info("Synchronisation reprogrammée toutes les %s minutes", minutes)


if __name__ == "__main__":
    uvicorn.run("start:app", host="0.0.0.0", port=8000, reload=True)
