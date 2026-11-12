"""Compose settings without shell evaluation; supports HTTPS on public IPv4."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def web_settings(host):
    host = host.strip().lower()
    ip_mode = False
    if host:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', host):
                raise ValueError('Enter a public IPv4 address or hostname only, without https://, port or path.')
        else:
            if address.version != 4 or not address.is_global:
                raise ValueError('Public HTTPS IP mode requires a public IPv4 address. Use a hostname or SSH tunnel otherwise.')
            ip_mode = True
    return {
        'N8N_HOST': host or 'localhost',
        'N8N_PROTOCOL': 'https' if host else 'http',
        'N8N_PUBLIC_URL': 'https://' + host if host else 'http://localhost:5678',
        'N8N_PROXY_HOPS': '1' if host else '0',
        'N8N_SECURE_COOKIE': 'true',
        'N8N_BIND': '127.0.0.1',
        'COMPOSE_PROFILES': 'https' if host else '',
        'CADDY_CONFIG': 'Caddyfile.ip' if ip_mode else 'Caddyfile',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image')
    parser.add_argument('--web', action='store_true')
    args = parser.parse_args()
    path = Path('.env')
    if args.image:
        if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}', args.image):
            raise SystemExit('Invalid image digest')
        text = re.sub(r'^N8N_IMAGE=.*$', 'N8N_IMAGE=' + args.image, path.read_text(), flags=re.M)
    else:
        values = {}
        if args.web:
            values = dict(line.split('=',1) for line in path.read_text().splitlines()
                          if line and not line.startswith('#') and '=' in line)
        else:
            values = {'N8N_IMAGE': 'docker.n8n.io/n8nio/n8n:stable',
                      'POSTGRES_PASSWORD': secrets.token_hex(32),
                      'N8N_ENCRYPTION_KEY': secrets.token_hex(32)}
        print('The n8n web UI can use HTTPS on your public IPv4, or a DNS hostname.')
        print('Ports 80/443 must reach this server and be free for Caddy. Blank uses an SSH tunnel.')
        while True:
            host = input('Server public IPv4 or hostname (blank = SSH tunnel): ').strip()
            try:
                values.update(web_settings(host))
                break
            except ValueError as exc:
                print(exc)
        text = ''.join(f'{k}={v}\n' for k,v in values.items())
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(text)
    os.chmod(temporary,0o600)
    temporary.replace(path)


if __name__ == '__main__':
    main()
