#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
  echo 'This installer targets Ubuntu 24.04.' >&2; exit 1
fi
apt-get update -qq
apt-get install -y ca-certificates curl git python3 openssl
if ! command -v docker >/dev/null; then
  # Ubuntu-maintained Docker packages. Do not replace an existing Docker installation.
  apt-get install -y docker.io docker-compose-v2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'Docker exists but Compose v2 is missing. Install the matching Compose plugin, then rerun.' >&2
  exit 1
fi
systemctl enable --now docker
install -d -m 750 -o root -g 1000 config
install -d -m 700 -o 1000 -g 1000 data
install -d -m 700 backups
if [[ ! -f .env ]]; then
  python3 scripts/environment.py
fi
chmod 600 .env
# Never source credentials as shell code.
if ! grep -q '^N8N_IMAGE=.*@sha256:' .env; then
  docker pull docker.n8n.io/n8nio/n8n:stable
  image_ref=$(docker image inspect docker.n8n.io/n8nio/n8n:stable --format '{{index .RepoDigests 0}}')
  python3 scripts/environment.py --image "$image_ref"
fi
docker compose config --quiet
docker compose build school-bridge
# The wizard verifies iCloud and writes a selected calendar URL. Secrets stay local.
if [[ ! -f config/settings.json ]]; then
  docker compose -f compose.yml -f compose.setup.yml run --rm --no-deps --user 0:0 school-bridge python setup.py
fi
chmod 640 config/settings.json
chown root:1000 config/settings.json
install -m 755 scripts/n8n-live /usr/local/bin/n8n-live
printf '%s\n' "$PWD" > /etc/waqaar-n8n.path
if [[ ! -e /usr/local/bin/update-live ]]; then
  ln -s /usr/local/bin/n8n-live /usr/local/bin/update-live
elif [[ $(readlink -f /usr/local/bin/update-live) != /usr/local/bin/n8n-live ]]; then
  echo 'Existing update-live command kept. Use n8n-live update for this project.'
fi
ln -sf /usr/local/bin/n8n-live /usr/local/bin/n8n-setup
if grep -qx 'COMPOSE_PROFILES=https' .env; then
  docker compose pull caddy
fi
docker compose up -d --wait --wait-timeout 180
python3 scripts/finish.py
