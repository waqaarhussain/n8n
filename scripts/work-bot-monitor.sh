#!/usr/bin/env bash
set -euo pipefail
cd -- "$(cat /etc/waqaar-n8n.path)"
exec /usr/bin/python3 scripts/work_bot.py monitor
