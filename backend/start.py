"""
ScholarSync — Point d'entrée avec scheduler de sync automatique
"""
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.main import app
from app.core.database import SessionLocal
from app.models import Parametre

scheduler = AsyncIOScheduler()

async def lancer_sync():
    """Tâche planifiée de synchronisation Zotero."""
    from app.sync.engine import sync_all
    db = SessionLocal()
    try:
        await sync_all(db)
    finally:
        db.close()

@app.on_event("startup")
async def startup():
    db = SessionLocal()
    try:
        row = db.query(Parametre).filter(Parametre.cle == "sync_intervalle_min").first()
        minutes = int(row.valeur) if row else 60
    finally:
        db.close()

    scheduler.add_job(
        lancer_sync,
        trigger=IntervalTrigger(minutes=minutes),
        id="sync_zotero",
        replace_existing=True,
    )
    scheduler.start()
    print(f"✓ Scheduler démarré — sync toutes les {minutes} minutes")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()

if __name__ == "__main__":
    uvicorn.run("start:app", host="0.0.0.0", port=8000, reload=True)
