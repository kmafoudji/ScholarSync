# ScholarSync

Plateforme open-source de signalement et de valorisation des mémoires et thèses académiques. Conçue pour être déployée par tout pays ou organisation souhaitant indexer leur production scientifique nationale.

## Fonctionnalités

- **Multi-sources Zotero** — synchronisation automatique depuis les groupes Zotero des institutions
- **Numérotation nationale** — identifiant unique de 16 caractères (ex. `SCUCTS2016000192` : établissement, type, statut, année, rang) avec clé de contrôle modulo 97, attribué à la soutenance ; code établissement géré depuis l'administration
- **Facettes avancées** — établissements, écoles doctorales, facultés, domaines CAMES, statut, année, langue
- **White-label** — nom, logo, couleurs et contenu entièrement configurables
- **Multi-langues** — interface en français, anglais et portugais
- **Contrôle d'accès granulaire** — par établissement, collection, sous-collection ou document
- **Export** — CSV, Excel, PDF pour les tableaux et graphiques
- **Tableau de bord** — statistiques nationales et par établissement

## Stack technique

| Composant | Technologie |
|---|---|
| Backend | FastAPI (Python) |
| Base de données | PostgreSQL |
| Moteur de recherche | Meilisearch |
| Frontend | Jinja2 + HTML/CSS/JS |
| Sync Zotero | pyzotero |
| Export | openpyxl, WeasyPrint |

## Structure du projet

```
ScholarSync/
├── backend/
│   ├── app/
│   │   ├── api/routes/     # Endpoints FastAPI
│   │   ├── core/           # Config, sécurité, auth
│   │   ├── models/         # Modèles SQLAlchemy
│   │   ├── schemas/        # Schémas Pydantic
│   │   ├── services/       # Logique métier
│   │   └── sync/           # Moteur de sync Zotero
│   └── migrations/         # Schémas SQL
├── frontend/
│   ├── src/
│   │   ├── components/     # Composants réutilisables
│   │   ├── pages/          # Pages publiques et admin
│   │   └── styles/         # CSS
└── docs/                   # Documentation
```

## Installation

```bash
# Cloner le repo
git clone https://github.com/kmafoudji/ScholarSync.git
cd ScholarSync

# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Base de données
psql -U postgres -d scholarsync -f migrations/001_initial_schema.sql
```

## Licence

MIT
