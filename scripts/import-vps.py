"""Import the opt-in weekly maintenance workflow. Password stays on this host."""
import getpass
import ipaddress
import json
from pathlib import Path
import subprocess

WORKFLOW_ID = 'VpsWeeklyUpdatesV1'


def run(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], check=True, **kwargs)


def main():
    # Refuse to replace a configured workflow or its credential on repeat imports.
    existing = run('exec', '-T', 'postgres', 'psql', '-X', '-qAt', '-U', 'n8n', '-d', 'n8n',
                   '-c', "SELECT count(*) FROM workflow_entity WHERE id='VpsWeeklyUpdatesV1';",
                   text=True, stdout=subprocess.PIPE).stdout.strip()
    if existing != '0':
        raise SystemExit('VPS weekly updates already exists. Edit its display-name node or SSH credential in n8n; nothing replaced.')
    host = input('VPS IP address: ').strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise SystemExit('Enter a valid VPS IP address. Nothing imported.')
    name = input('VPS display name: ').strip()
    if not name:
        raise SystemExit('A display name is required.')
    if len(name) > 100 or any(not c.isprintable() for c in name):
        raise SystemExit('Use a display name of 1–100 characters without control characters.')
    password = getpass.getpass('Root SSH password (hidden): ')
    if not password:
        raise SystemExit('Password is required. Nothing imported.')
    workflow = json.loads(Path('workflows/vps-weekly-updates.json').read_text())
    workflow['nodes'][2]['parameters']['jsCode'] = 'return [{json: {vps_name: ' + json.dumps(name) + '}}];'
    credential = [{'id': 'VpsWeeklySsh1', 'name': 'Weekly VPS SSH', 'type': 'sshPassword',
                   'data': {'host': host, 'port': 22, 'username': 'root', 'password': password}}]
    jobs = [('/tmp/waqaar-vps-credential.json', credential, 'credentials'),
            ('/tmp/waqaar-vps-workflow.json', workflow, 'workflow')]
    try:
        for path, value, kind in jobs:
            run('exec', '-T', '--user', '0:0', 'n8n', 'sh', '-c',
                f'umask 077; cat > {path}; chown node:node {path}', input=json.dumps(value).encode())
            run('exec', '-T', 'n8n', 'n8n', 'import:' + kind, '--input=' + path)
    finally:
        run('exec', '-T', '--user', '0:0', 'n8n', 'rm', '-f', *(p for p, _, _ in jobs))
    print('Imported INACTIVE: VPS weekly updates. No packages have been changed.')
    print('In n8n, verify the SSH credential, then publish for Mondays 00:00 Europe/London.')
    print('Run sudo telegram and enable this workflow for failure and reboot-required alerts. No automatic reboot.')


if __name__ == '__main__':
    main()
