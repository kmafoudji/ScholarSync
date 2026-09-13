#!/usr/bin/env bash
# Génère les secrets manquants dans .env, sans écraser ceux déjà présents.
#
# Sur une installation existante, POSTGRES_PASSWORD n'est PAS généré : le
# volume PostgreSQL garde le mot de passe avec lequel la base a été créée,
# et en changer un seul côté empêche l'application de se connecter.
set -euo pipefail

cd "$(dirname "$0")/.."

command -v openssl >/dev/null || { echo "openssl est requis"; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ .env créé à partir de .env.example"
fi

# Une base déjà créée ? Son volume porte le mot de passe d'origine.
BASE_EXISTANTE=0
COMPOSE="docker compose"
$COMPOSE version >/dev/null 2>&1 || COMPOSE="docker-compose"
if $COMPOSE version >/dev/null 2>&1; then
  if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q 'scholarsync-db-data'; then
    BASE_EXISTANTE=1
  fi
fi

remplir() {
  local cle="$1" valeur="$2"
  if grep -qE "^${cle}=.+" .env; then
    echo "   ${cle} : déjà défini, inchangé"
  else
    awk -v c="$cle" -v v="$valeur" \
      'BEGIN{FS=OFS="="} $1==c {print c "=" v; fait=1; next} {print} END{if(!fait) print c "=" v}' \
      .env > .env.tmp && mv .env.tmp .env
    echo "   ${cle} : généré"
  fi
}

echo "Secrets :"

# SECRET_KEY se régénère sans risque : elle ne fait que signer les cookies.
# La changer déconnecte les sessions ouvertes, rien de plus.
remplir SECRET_KEY "$(openssl rand -hex 32)"

if [ "$BASE_EXISTANTE" -eq 1 ] && ! grep -qE "^POSTGRES_PASSWORD=.+" .env; then
  echo
  echo "   ⚠ POSTGRES_PASSWORD : NON généré."
  echo
  echo "     Le volume scholarsync-db-data existe déjà : la base a été créée"
  echo "     avec un mot de passe que PostgreSQL a enregistré. En générer un"
  echo "     nouveau ici ferait échouer toutes les connexions."
  echo
  echo "     Renseignez dans .env le mot de passe actuel de la base. S'il"
  echo "     venait de l'ancien docker-compose.yml versionné :"
  echo
  echo "       git show \$(git log --format=%H -1 -- docker-compose.yml)~1:docker-compose.yml | grep POSTGRES_PASSWORD"
  echo
  echo "     Pour le changer proprement ensuite, voir « Changer le mot de"
  echo "     passe d'une base existante » dans docs/deploiement.md."
  echo
else
  remplir POSTGRES_PASSWORD "$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
fi

chmod 600 .env
echo "✓ .env prêt (permissions 600)."
