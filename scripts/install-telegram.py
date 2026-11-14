"""Install/restart the opt-in alert monitor. Installing never subscribes workflows."""
from pathlib import Path
import shutil
import subprocess

UNIT = '''[Unit]
Description=n8n opt-in Telegram failure alerts
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/n8n-telegram-monitor
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
'''


def main():
    target = Path('/usr/local/bin/n8n-telegram-monitor')
    shutil.copyfile('scripts/telegram-monitor.sh', target)
    target.chmod(0o755)
    Path('/etc/systemd/system/n8n-telegram.service').write_text(UNIT)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'n8n-telegram.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'n8n-telegram.service'], check=True)
    print('Telegram monitor installed. No new workflow subscriptions added.')


if __name__ == '__main__':
    main()
