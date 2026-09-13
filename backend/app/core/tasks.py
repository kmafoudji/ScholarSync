"""
Tâches de fond.

Une tâche lancée depuis une requête HTTP ne peut pas réutiliser la session
SQLAlchemy de cette requête : `get_db()` la ferme dès la réponse envoyée, et
la tâche se retrouve à travailler sur une session morte. Chaque tâche ouvre
donc la sienne et la referme elle-même.

Le SDK Zotero (pyzotero) fait des appels HTTP bloquants. Les exécuter dans la
boucle d'événements figerait tout le serveur pendant la durée de la sync ;
elles tournent donc dans un thread dédié.
"""
import logging
import threading
from typing import Callable, Optional, Set

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

# Sources en cours de synchronisation (processus courant).
# La source de vérité reste SyncLog en base ; ceci évite juste de relancer
# deux fois la même sync et alimente l'indicateur temps réel de l'interface.
_running: Set[str] = set()
_lock = threading.Lock()


def is_running(key: str = "global") -> bool:
    with _lock:
        return key in _running


def running_keys() -> Set[str]:
    with _lock:
        return set(_running)


def run_in_background(
    fn: Callable, *args, key: Optional[str] = None, **kwargs
) -> bool:
    """
    Exécute `fn(db, *args, **kwargs)` dans un thread, avec une session
    dédiée. Retourne False si une tâche portant la même clé tourne déjà.
    """
    key = key or "global"

    with _lock:
        if key in _running:
            return False
        _running.add(key)

    def _worker():
        db = SessionLocal()
        try:
            fn(db, *args, **kwargs)
        except Exception:
            logger.exception("Tâche de fond '%s' interrompue", key)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
            with _lock:
                _running.discard(key)

    threading.Thread(target=_worker, name=f"task:{key}", daemon=True).start()
    return True
