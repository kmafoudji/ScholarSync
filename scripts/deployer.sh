#!/usr/bin/env bash
# Met à jour une instance ScholarSync déjà en service.
#
# Conserve la base de données et les fichiers téléversés. Sauvegarde la
# base avant toute chose : une migration ratée sur une instance en
# production sans sauvegarde ne se rattrape pas.
set -euo pipefail

cd "$(dirname "$0")/.."
RACINE="$(pwd)"
HORODATAGE="$(date +%Y%m%d-%H%M%S)"
SAUVEGARDES="${RACINE}/sauvegardes"

info() { printf '\n\033[1;34m→ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
avert(){ printf '  \033[0;33m!\033[0m %s\n' "$*"; }

# ── Vérifications préalables ────────────────────────────────────────
info "Vérifications"

if [ ! -f .env ]; then
  avert ".env absent — exécutez d'abord ./scripts/generer-secrets.sh"
  exit 1
fi
# shellcheck disable=SC1091
# Clé du moteur de recherche, apparue avec Meilisearch : une installation
# antérieure ne l'a pas encore. On la crée plutôt que d'échouer.
if ! grep -qE "^MEILI_MASTER_KEY=.+" .env; then
  command -v openssl >/dev/null && {
    grep -q "^MEILI_MASTER_KEY=" .env && sed -i '/^MEILI_MASTER_KEY=/d' .env
    [ -n "$(tail -c1 .env)" ] && echo >> .env
    echo "MEILI_MASTER_KEY=$(openssl rand -hex 24)" >> .env
    ok "MEILI_MASTER_KEY générée dans .env"
  }
fi
set -a; . ./.env; set +a
: "${SECRET_KEY:?SECRET_KEY vide dans .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD vide dans .env}"
ok ".env chargé"

COMPOSE="docker compose"
$COMPOSE version >/dev/null 2>&1 || COMPOSE="docker-compose"
$COMPOSE version >/dev/null 2>&1 || { avert "docker compose introuvable"; exit 1; }
ok "$COMPOSE disponible"

# ── Sauvegarde ──────────────────────────────────────────────────────
info "Sauvegarde avant mise à jour"
mkdir -p "$SAUVEGARDES"

if $COMPOSE ps --status running 2>/dev/null | grep -q scholarsync-db; then
  FICHIER="${SAUVEGARDES}/base-${HORODATAGE}.sql.gz"
  $COMPOSE exec -T scholarsync-db \
    pg_dump -U "${POSTGRES_USER:-scholarsync}" "${POSTGRES_DB:-scholarsync}" \
    | gzip > "$FICHIER"
  # Un dump vide signale un échec que le pipe masquerait
  if [ ! -s "$FICHIER" ] || [ "$(gzip -dc "$FICHIER" | head -c 20 | wc -c)" -lt 20 ]; then
    avert "La sauvegarde est vide — arrêt avant toute modification."
    exit 1
  fi
  ok "Base sauvegardée : $(du -h "$FICHIER" | cut -f1) → ${FICHIER#$RACINE/}"
else
  avert "Base non démarrée : aucune sauvegarde (première installation ?)"
fi

if [ -d uploads ] && [ -n "$(ls -A uploads 2>/dev/null)" ]; then
  tar czf "${SAUVEGARDES}/uploads-${HORODATAGE}.tar.gz" uploads
  ok "Téléversements sauvegardés"
fi

# ── Récupération du code ────────────────────────────────────────────
info "Récupération du code"
if [ -d .git ]; then
  if [ -n "$(git status --porcelain)" ]; then
    avert "Modifications locales non commitées :"
    git status --short | sed 's/^/      /'
    avert "Elles seront conservées, mais peuvent entrer en conflit."
  fi
  BRANCHE="$(git rev-parse --abbrev-ref HEAD)"
  git fetch origin "$BRANCHE"
  AVANT="$(git rev-parse HEAD)"
  git merge --ff-only "origin/${BRANCHE}" || {
    avert "Avance rapide impossible — l'historique local a divergé."
    avert "Résolvez à la main : git log --oneline HEAD..origin/${BRANCHE}"
    exit 1
  }
  APRES="$(git rev-parse HEAD)"
  if [ "$AVANT" = "$APRES" ]; then
    ok "Déjà à jour ($(git log --oneline -1))"
  else
    ok "Mis à jour : $(git log --oneline -1)"
    git --no-pager log --oneline "${AVANT}..${APRES}" | sed 's/^/      /'
  fi
else
  avert "Pas de dépôt git ici — copiez le code manuellement puis relancez."
  exit 1
fi

# ── Reconstruction et redémarrage ───────────────────────────────────
info "Reconstruction de l'image"
$COMPOSE build scholarsync-app
ok "Image construite"

info "Dossier des téléversements"
# Le conteneur tourne sous l'UID 1000 (utilisateur scholarsync) et écrit
# les logos dans ./uploads, monté depuis l'hôte. Un dossier créé par
# root — ou par Docker lui-même au premier montage — lui est interdit
# en écriture : chaque téléversement de logo échouait alors. On le
# remet d'aplomb à chaque déploiement plutôt que de compter sur une
# commande manuelle oubliée.
UID_APP=1000
mkdir -p uploads
if [ "$(id -u)" = "0" ]; then
  chown -R "${UID_APP}:${UID_APP}" uploads
  ok "uploads appartient à l'UID ${UID_APP}"
elif [ "$(stat -c %u uploads)" != "${UID_APP}" ]; then
  avert "uploads n'appartient pas à l'UID ${UID_APP} : les logos ne pourront pas être enregistrés."
  avert "Corrigez avec : sudo chown -R ${UID_APP}:${UID_APP} $(pwd)/uploads"
else
  ok "uploads accessible en écriture"
fi

info "Redémarrage"
$COMPOSE up -d
ok "Conteneurs relancés"

# ── Contrôle de santé ───────────────────────────────────────────────
info "Contrôle de santé"
for essai in $(seq 1 30); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/sante >/dev/null 2>&1; then
    ok "L'application répond (après ${essai}0 s au plus)"
    $COMPOSE ps --format '      {{.Name}}  {{.Status}}' 2>/dev/null || true
    echo
    ok "Mise à jour terminée."
    exit 0
  fi
  sleep 2
done

avert "L'application ne répond pas après 60 s. Journaux récents :"
$COMPOSE logs --tail 40 scholarsync-app
avert "Pour revenir en arrière :"
avert "  git reset --hard ${AVANT} && $COMPOSE up -d --build"
avert "  puis restaurer la base depuis ${SAUVEGARDES}/base-${HORODATAGE}.sql.gz"
exit 1
