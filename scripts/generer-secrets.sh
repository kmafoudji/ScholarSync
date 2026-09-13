#!/usr/bin/env bash
# Génère les secrets manquants dans .env, sans écraser ceux déjà présents.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ .env créé à partir de .env.example"
fi

remplir() {
  local cle="$1" valeur="$2"
  if grep -qE "^${cle}=.+" .env; then
    echo "   ${cle} : déjà défini, inchangé"
  else
    # Le remplacement passe par un fichier temporaire : sed -i diffère
    # entre GNU et BSD, et la valeur peut contenir des caractères spéciaux.
    awk -v c="$cle" -v v="$valeur" \
      'BEGIN{FS=OFS="="} $1==c {print c "=" v; fait=1; next} {print} END{if(!fait) print c "=" v}' \
      .env > .env.tmp && mv .env.tmp .env
    echo "   ${cle} : généré"
  fi
}

command -v openssl >/dev/null || { echo "openssl est requis"; exit 1; }

echo "Secrets :"
remplir SECRET_KEY "$(openssl rand -hex 32)"
remplir POSTGRES_PASSWORD "$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"

chmod 600 .env
echo
echo "✓ .env prêt (permissions 600)."
echo "  Sauvegardez POSTGRES_PASSWORD ailleurs : le changer après coup"
echo "  exige de modifier aussi le mot de passe dans PostgreSQL."
