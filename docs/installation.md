# Installation ScholarSync

## Prérequis

- Python 3.11+
- PostgreSQL 14+
- (Optionnel) Meilisearch pour la recherche full-text avancée

## 1. Cloner et configurer

```bash
git clone https://github.com/kmafoudji/ScholarSync.git
cd ScholarSync/backend
cp .env.example .env
# Éditez .env avec vos paramètres
```

## 2. Base de données PostgreSQL

```bash
sudo -u postgres psql
CREATE DATABASE scholarsync;
CREATE USER scholarsync WITH PASSWORD 'votre_mot_de_passe';
GRANT ALL PRIVILEGES ON DATABASE scholarsync TO scholarsync;
\q

psql -U scholarsync -d scholarsync -f migrations/001_initial_schema.sql
```

## 3. Environnement Python

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Lancer l'application

```bash
# Développement
python start.py

# Production (avec gunicorn)
gunicorn start:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## 5. Accéder à l'application

- **Site public** : http://localhost:8000
- **Administration** : http://localhost:8000/admin
- **API docs** : http://localhost:8000/api/docs

## 6. Configuration initiale

1. Allez dans **Admin → Paramètres → Identité visuelle**
2. Configurez le nom de l'outil, logo, couleurs
3. Allez dans **Admin → Zotero → Ajouter un compte**
4. Entrez l'ID et la clé API de votre groupe Zotero
5. Lancez une première synchronisation manuelle

## Structure des groupes Zotero

```
Groupe Zotero [Établissement]
├── Thèses
│   ├── École doctorale Sciences de la Vie
│   └── École doctorale Droit
└── Mémoires
    ├── UFR Sciences de la Santé
    └── Faculté de Droit
```

**Tags obligatoires sur chaque item :**
```
statut: soutenu       (ou) statut: en_preparation
domaine: Sciences de la santé   (domaine CAMES)
```

## Déploiement VPS (Nginx + Gunicorn)

```nginx
server {
    server_name scholarsync.lab-me.online;
    
    location /static/ {
        alias /var/www/scholarsync/backend/app/static/;
    }
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
# Service systemd
sudo nano /etc/systemd/system/scholarsync.service
```

```ini
[Unit]
Description=ScholarSync
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/scholarsync/backend
ExecStart=/var/www/scholarsync/venv/bin/gunicorn start:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
```
