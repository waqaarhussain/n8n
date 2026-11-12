"""Import the local iCloud credential and workflow without printing secrets."""
import json
from pathlib import Path
import subprocess


def run(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], check=True, **kwargs)


def main():
    settings = json.loads(Path('config/settings.json').read_text())
    mail = settings.get('mail', {})
    if mail.get('mode') != 'icloud_alias' or not all(mail.get(k) for k in ('alias', 'username', 'password')):
        raise SystemExit('Run sudo n8n-setup first to configure the iCloud mailbox and automation alias.')
    credential_id = 'SchoolIcloudImap'
    credential = [{'id': credential_id, 'name': 'School iCloud IMAP', 'type': 'imap', 'data': {
        'user': mail['username'], 'password': mail['password'], 'host': 'imap.mail.me.com',
        'port': 993, 'secure': True, 'allowUnauthorizedCerts': False,
    }}]
    workflow = json.loads(Path('workflows/school-icloud.json').read_text())
    trigger = workflow['nodes'][0]
    trigger['parameters']['options']['customEmailConfig'] = json.dumps(['ALL', ['TO', mail['alias']]])
    trigger['credentials'] = {'imap': {'id': credential_id, 'name': 'School iCloud IMAP'}}
    # Fixed, task-owned temporary paths. Credentials are removed even if import fails.
    jobs = [('/tmp/waqaar-school-credential.json', credential, 'credentials'),
            ('/tmp/waqaar-school-workflow.json', workflow, 'workflow')]
    try:
        for path, value, kind in jobs:
            command = f'umask 077; cat > {path}; chown node:node {path}'
            run('exec', '-T', '--user', '0:0', 'n8n', 'sh', '-c', command,
                input=json.dumps(value).encode())
            run('exec', '-T', 'n8n', 'n8n', 'import:' + kind, '--input=' + path)
    finally:
        run('exec', '-T', '--user', '0:0', 'n8n', 'rm', '-f', *(p for p, _, _ in jobs))
    print('Imported an INACTIVE iCloud workflow with the configured alias and IMAP credential.')
    print('Open it in n8n, verify the alias, test once, then publish.')


if __name__ == '__main__':
    main()
