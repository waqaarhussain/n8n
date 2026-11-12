#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi
apt-get update -qq
apt-get install -y git curl ca-certificates python3
if [[ ! -d /opt/waqaar-n8n/.git ]]; then
  if [[ -e /opt/waqaar-n8n ]]; then
    echo '/opt/waqaar-n8n already exists and is not this checkout. Nothing overwritten.' >&2
    exit 1
  fi
  git clone https://github.com/waqaarhussain/n8n.git /opt/waqaar-n8n
fi
exec bash /opt/waqaar-n8n/install.sh
