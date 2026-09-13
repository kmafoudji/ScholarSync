# Déploiement et mise à jour

Ce document couvre le déploiement Docker et la mise à jour d'une instance
déjà en service. Pour une installation Python sans Docker, voir
[installation.md](installation.md).

---

## ⚠️ Rotation de la clé de signature — à faire en premier

Les versions antérieures de `docker-compose.yml` contenaient une
`SECRET_KEY` et un mot de passe PostgreSQL **en clair dans un dépôt
public**. Cette clé signe les cookies de session : quiconque la connaît
peut forger un cookie d'administrateur sur une instance qui l'utilise.

Elle reste lisible dans l'historique Git même après correction du fichier.
Toute instance déployée avec cette valeur doit donc changer de clé, et non
simplement mettre à jour le code.

```bash
cd /chemin/vers/ScholarSync
cp .env.example .env
./scripts/generer-secrets.sh      # génère SECRET_KEY et POSTGRES_PASSWORD
docker compose up -d
```

Changer `SECRET_KEY` invalide toutes les sessions ouvertes : chaque
administrateur devra se reconnecter. C'est le comportement attendu — les
sessions signées avec l'ancienne clé doivent cesser d'être valides.

> **Le mot de passe PostgreSQL est différent.** Si la base existe déjà, le
> changer dans `.env` ne le change pas dans PostgreSQL, et l'application
> ne pourra plus se connecter. `scripts/generer-secrets.sh` détecte
> désormais le volume `scholarsync-db-data` et refuse d'en générer un
> nouveau dans ce cas ; il faut alors reporter le mot de passe **actuel**
> de la base dans `.env`.
>
> Symptôme si les deux valeurs divergent :
>
> ```
> FATAL: password authentication failed for user "scholarsync"
> ```
>
> Le conteneur redémarre en boucle (`Restarting`) et le reverse proxy
> renvoie **502 Bad Gateway**. Récupérer l'ancien mot de passe :
>
> ```bash
> git show $(git log --format=%H -1 -- docker-compose.yml)~1:docker-compose.yml \
>   | grep POSTGRES_PASSWORD
> ```

---

## Mettre à jour une instance en service

`scripts/deployer.sh` enchaîne les étapes dans l'ordre sûr : sauvegarde,
récupération du code, reconstruction, redémarrage, contrôle de santé.

```bash
cd /chemin/vers/ScholarSync
./scripts/deployer.sh
```

Le script s'arrête net si la sauvegarde de la base échoue ou revient
vide — mieux vaut ne pas mettre à jour que mettre à jour sans filet. Les
sauvegardes s'écrivent dans `sauvegardes/`, horodatées.

### Colonnes ajoutées après coup

`Base.metadata.create_all` crée les tables manquantes mais n'ajoute jamais
une colonne à une table existante. Les colonnes introduites après une mise
en production sont déclarées dans `app/core/schema.py` et appliquées au
démarrage, de façon idempotente. Aucune action manuelle n'est requise.

### Revenir en arrière

```bash
git reset --hard <commit-précédent>
docker compose up -d --build

# Si la base a été modifiée entre-temps :
gunzip -c sauvegardes/base-AAAAMMJJ-HHMMSS.sql.gz \
  | docker compose exec -T scholarsync-db psql -U scholarsync scholarsync
```

---

## Première installation

```bash
git clone https://github.com/kmafoudji/ScholarSync.git
cd ScholarSync
cp .env.example .env
./scripts/generer-secrets.sh
docker compose up -d --build
```

Puis ouvrez `http://<serveur>:8000/admin/setup` pour créer le premier
compte administrateur.

---

## Reverse proxy et HTTPS

`docker-compose.yml` publie l'application sur `127.0.0.1:8000` uniquement :
elle n'est pas joignable depuis l'extérieur sans reverse proxy. C'est
voulu — le TLS se termine au proxy.

```nginx
server {
    listen 80;
    server_name exemple.sn;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name exemple.sn;

    ssl_certificate     /etc/letsencrypt/live/exemple.sn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/exemple.sn/privkey.pem;

    # Les logos téléversés peuvent atteindre 2 Mo (MAX_LOGO_SIZE_MB).
    client_max_body_size 4M;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Un export PDF ou un lancement de sync peut dépasser 60 s
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo certbot --nginx -d exemple.sn
```

Pour un accès direct sans proxy (test uniquement), remplacez dans
`docker-compose.yml` :

```yaml
ports:
  - "0.0.0.0:8000:8000"
```

---

## Dossier des téléversements

Le conteneur tourne sous un compte sans privilèges (UID 1000). Le dossier
`uploads/` monté depuis l'hôte doit lui appartenir, sinon l'enregistrement
du logo échoue :

```bash
mkdir -p uploads
sudo chown -R 1000:1000 uploads
```

> Les logos téléversés **avant** la correction du montage n'ont jamais
> atteint l'hôte : ils vivaient dans la couche d'écriture du conteneur,
> perdue à chaque reconstruction. Il faut les téléverser à nouveau — une
> seule fois, ils persisteront ensuite.

---

## Sauvegardes

`scripts/deployer.sh` sauvegarde avant chaque mise à jour, mais cela ne
remplace pas une sauvegarde périodique :

```bash
# crontab -e — tous les jours à 3 h
0 3 * * * cd /chemin/vers/ScholarSync && docker compose exec -T scholarsync-db \
  pg_dump -U scholarsync scholarsync | gzip > sauvegardes/base-$(date +\%F).sql.gz
```

Les sauvegardes vivent sur le même disque que la base : copiez-les
ailleurs, sinon une panne de disque emporte les deux.

---

## Nombre de workers

L'image lance gunicorn avec **un seul worker**, volontairement. Le
planificateur de synchronisation (APScheduler, dans `start.py`) démarre
avec chaque worker : avec `-w 4`, quatre planificateurs déclencheraient la
même synchronisation en parallèle sur la même bibliothèque Zotero.

Un worker suffit largement pour un catalogue national consulté par un
public modeste : les requêtes sont courtes et la synchronisation tourne
dans un thread séparé. Si la charge l'exige, la bonne réponse est de
sortir le planificateur de l'application (conteneur dédié, ou `cron`
appelant l'API) avant d'augmenter le nombre de workers.

---

## Changer le mot de passe d'une base existante

Modifier `POSTGRES_PASSWORD` dans `.env` ne change pas le mot de passe
déjà enregistré dans PostgreSQL : les deux doivent être changés ensemble.

```bash
# 1. Changer le mot de passe dans PostgreSQL
docker compose exec scholarsync-db \
  psql -U scholarsync -c "ALTER USER scholarsync WITH PASSWORD 'nouveau';"

# 2. Reporter la même valeur dans .env, puis
docker compose up -d
```

---

## Diagnostic

```bash
docker compose ps                             # état des conteneurs
docker compose logs -f --tail 100 scholarsync-app
curl -s http://127.0.0.1:8000/sante           # {"statut":"ok","base":"ok"}
```

| Symptôme | Cause probable |
|---|---|
| `SECRET_KEY manquant dans .env` au démarrage | `.env` absent ou vide — lancez `./scripts/generer-secrets.sh` |
| **502 Bad Gateway** et conteneur `Restarting` | Voir les journaux : le plus souvent `password authentication failed`, c'est-à-dire `.env` désaccordé avec le volume PostgreSQL |
| Les changements de code ne prennent pas effet | `docker compose up -d` ne reconstruit pas une image existante — utilisez `up -d --build` |
| `/sante` renvoie 503 `base: injoignable` | PostgreSQL non démarré, ou mot de passe désaccordé avec `.env` |
| Le logo téléversé disparaît au redémarrage | Montage `uploads` erroné — vérifiez `./uploads:/app/app/static/img/uploads` |
| « Le dossier des téléversements n'est pas accessible en écriture » | Le conteneur tourne sous l'UID 1000 : `mkdir -p uploads && sudo chown -R 1000:1000 uploads` |
| Les graphiques du tableau de bord restent vides | Chart.js vient d'un CDN : vérifiez l'accès sortant du navigateur client |
| La synchronisation ne démarre jamais | Aucune source Zotero active — voir Administration → Comptes autorisés |
