FROM python:3.11-slim

# WeasyPrint (export PDF) a besoin de Pango et Cairo au moment de
# l'exécution, pas seulement à la compilation : sans eux les exports
# échouent au premier appel, pas au démarrage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY backend/ .

# Le code vit dans /app/app : les téléversements aussi.
RUN mkdir -p app/static/img/uploads

# Exécution sans privilèges : une faille dans l'application ne donne pas
# root dans le conteneur.
# UID fixé à 1000 : le dossier uploads monté depuis l'hôte doit
# appartenir au même identifiant, sinon l'écriture est refusée.
#   sur le serveur : chown -R 1000:1000 uploads
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin scholarsync \
    && chown -R scholarsync:scholarsync /app
USER scholarsync

EXPOSE 8000

# start.py porte le planificateur de synchronisation : c'est lui le point
# d'entrée, pas app.main directement.
#
# Un seul worker : le planificateur APScheduler s'exécute au démarrage de
# chaque worker. Avec -w 4, quatre planificateurs déclencheraient la même
# synchronisation en parallèle sur la même bibliothèque Zotero.
CMD ["gunicorn", "start:app", \
     "-w", "1", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--access-logfile", "-"]
