"""Write Compose settings without shell evaluation or unsafe interpolation."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets

p = argparse.ArgumentParser()
p.add_argument('--image')
a = p.parse_args()
path = Path('.env')
if a.image:
    if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}', a.image):
        raise SystemExit('Invalid image digest')
    text = re.sub(r'^N8N_IMAGE=.*$', 'N8N_IMAGE=' + a.image, path.read_text(), flags=re.M)
else:
    host = input('Hostname for HTTPS (e.g. n8n.example.com); blank = local SSH tunnel: ').strip().lower()
    if host:
        if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', host):
            raise SystemExit('Enter a hostname only, without https://, port or path.')
        try:
            ipaddress.ip_address(host)
            raise SystemExit('Use a DNS hostname for HTTPS, or leave blank for SSH tunnel.')
        except ValueError:
            pass
    values = {
        'N8N_IMAGE': 'docker.n8n.io/n8nio/n8n:stable',
        'POSTGRES_PASSWORD': secrets.token_hex(32),
        'N8N_ENCRYPTION_KEY': secrets.token_hex(32),
        'N8N_HOST': host or 'localhost',
        'N8N_PROTOCOL': 'https' if host else 'http',
        'N8N_PUBLIC_URL': 'https://' + host if host else 'http://localhost:5678',
        'N8N_PROXY_HOPS': '1' if host else '0',
        'N8N_SECURE_COOKIE': 'true',
        'N8N_BIND': '127.0.0.1',
        'COMPOSE_PROFILES': 'https' if host else '',
    }
    text = ''.join(f'{k}={v}\n' for k, v in values.items())
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(text)
os.chmod(path, 0o600)
