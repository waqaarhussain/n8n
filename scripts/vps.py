"""Private multi-VPS inventory, workflow generation and restricted reboot keys."""
import ast
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import urlsplit

CONFIG = Path('config/vps.json')
KEYS = Path('config/vps-keys')
WORKFLOW_ID = 'VpsWeeklyUpdatesV1'
# This dedicated key cannot execute arbitrary commands or forward connections.
FORCED_COMMAND = "flock -n /var/lock/n8n-weekly-apt.lock sh -c 'test ! -e /run/n8n-reboot-pending && /sbin/shutdown -r +1 && touch /run/n8n-reboot-pending'"


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=kwargs.pop('timeout', 120), **kwargs)


def docker(*args, **kwargs):
    return run('docker', 'compose', *args, **kwargs)


def load():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {'servers': [], 'telegram_reboot_enabled': False}


def save(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    temporary.replace(CONFIG)


def address(value):
    parsed = ipaddress.ip_address(value)
    return str(getattr(parsed, 'ipv4_mapped', None) or parsed)


def self_addresses():
    """Refresh protection from editor URL and local interfaces, not Ubuntu hostname."""
    addresses = {'127.0.0.1', '::1'}
    if Path('.env').exists():
        values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines()
                      if line and not line.startswith('#') and '=' in line)
        host = urlsplit(values.get('N8N_PUBLIC_URL', '')).hostname
        if host:
            addresses.add(host)
    try:
        for interface in json.loads(run('ip', '-j', 'address', 'show', timeout=5).stdout):
            addresses.update(x['local'] for x in interface.get('addr_info', []) if 'local' in x)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    normalized = set()
    for value in addresses:
        try: normalized.add(address(value))
        except ValueError: normalized.add(value)
    return normalized


def protected(server, settings=None):
    settings = load() if settings is None else settings
    return (server.get('is_n8n_host', False) or address(server['host']) in self_addresses()
            or address(server['host']) in {address(h) for h in settings.get('protected_hosts', [])})


def eligible(settings=None):
    settings = load() if settings is None else settings
    return [s for s in settings['servers'] if s.get('reboot_ready') and not protected(s, settings)]


def credential(identity):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
        raise ValueError('Invalid saved credential identity')
    path = '/tmp/waqaar-vps-' + secrets.token_hex(12) + '.json'
    try:
        docker('exec', '-T', 'n8n', 'sh', '-c',
               'umask 077; n8n export:credentials --id=' + identity + ' --decrypted --output=' + path)
        result = json.loads(docker('exec', '-T', 'n8n', 'cat', path).stdout)
        if len(result) != 1 or result[0].get('type') != 'sshPassword':
            raise ValueError('Expected one saved password SSH credential')
        return result[0]['data']
    finally:
        docker('exec', '-T', 'n8n', 'rm', '-f', path)


def import_value(value, kind):
    path = '/tmp/waqaar-vps-' + secrets.token_hex(12) + '.json'
    try:
        docker('exec', '-T', 'n8n', 'sh', '-c', 'umask 077; cat > ' + path, input=json.dumps(value))
        docker('exec', '-T', 'n8n', 'n8n', 'import:' + kind, '--input=' + path)
    finally:
        docker('exec', '-T', 'n8n', 'rm', '-f', path)


def setup_key(server, data):
    if protected(server):
        raise ValueError('The n8n host cannot receive a Telegram reboot key')
    if address(data['host']) != address(server['host']) or int(data.get('port', 22)) != int(server.get('port', 22)) or data.get('username') != 'root':
        raise ValueError('Saved SSH credential target differs from the VPS inventory')
    KEYS.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(KEYS, 0o700)
    key = KEYS/server['id']
    if not key.exists():
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key), '-C', 'n8n-reboot-only')
    os.chmod(key, 0o600)
    payload = {'credential': data, 'public_key': key.with_suffix('.pub').read_text().strip(),
               'forced_command': FORCED_COMMAND, 'expected_key': server.get('host_key', '')}
    output = docker('exec', '-T', 'n8n', 'node', '-e', Path('scripts/vps-ssh-setup.cjs').read_text(),
                    input=json.dumps(payload), timeout=45)
    result = json.loads(output.stdout)
    host_key = result['host_key']
    if not re.fullmatch(r'[A-Za-z0-9@._+-]+ [A-Za-z0-9+/=]+', host_key):
        raise ValueError('Invalid SSH host key')
    server['host_key'] = host_key
    server['reboot_ready'] = True
    known = KEYS/(server['id'] + '.known_hosts')
    label = server['host'] if int(server.get('port', 22)) == 22 else '[' + server['host'] + ']:' + str(server['port'])
    known.write_text(label + ' ' + host_key + '\n')
    known.chmod(0o600)
    print('Restricted reboot key installed for: ' + server['name'] + ' (SSH host key pinned).')


def reboot(server_id):
    settings = load()
    server = next((s for s in settings['servers'] if s['id'] == server_id), None)
    if not settings.get('telegram_reboot_enabled') or server is None or protected(server, settings) or not server.get('reboot_ready'):
        raise ValueError('Server is not permitted for Telegram reboot')
    if not re.fullmatch(r'[a-f0-9]{12}', server_id):
        raise ValueError('Invalid server identity')
    key, known = KEYS/server_id, KEYS/(server_id+'.known_hosts')
    if not key.is_file() or not known.is_file():
        raise ValueError('Reboot key is unavailable; use the VPS manager')
    # Remote authorized_keys forces FORCED_COMMAND even if this argument is changed.
    run('ssh', '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known),
        '-o', 'ConnectTimeout=10', '-p', str(server.get('port', 22)),
        'root@' + server['host'], 'reboot', timeout=25)


def migrate(settings):
    if settings['servers']:
        return
    raw = docker('exec', '-T', 'postgres', 'psql', '-X', '-qAt', '-U', 'n8n', '-d', 'n8n',
                 '-c', "SELECT row_to_json(w) FROM (SELECT nodes FROM workflow_entity WHERE id='VpsWeeklyUpdatesV1') w;").stdout.strip()
    if not raw:
        return
    nodes = json.loads(raw)['nodes']
    ssh = next(n for n in nodes if n['type'] == 'n8n-nodes-base.ssh')
    cred_id = ssh['credentials']['sshPassword']['id']
    data = credential(cred_id)
    # Legacy display name was a JSON string constant in this task-owned node.
    code = next(n for n in nodes if n.get('name') == 'VPS display name')['parameters']['jsCode']
    match = re.search(r'vps_name:\s*("(?:\\.|[^"\\])*"|\'[^\']*\')', code)
    name = ast.literal_eval(match.group(1)) if match else input('Existing VPS display name: ').strip()
    server = {'id': secrets.token_hex(6), 'host': address(data['host']),
              'port': int(data.get('port', 22)), 'name': name, 'credential_id': cred_id,
              'credential_name': ssh['credentials']['sshPassword'].get('name', 'Weekly VPS SSH'),
              'is_n8n_host': False, 'reboot_ready': False}
    server['is_n8n_host'] = protected(server, settings)
    settings['servers'].append(server)
    save(settings)
    print('Kept existing VPS: ' + name)


def add(settings):
    while True:
        host = input('VPS IP to add [Enter finishes]: ').strip()
        if not host:
            return
        host = address(host)
        if any(s['host'] == host for s in settings['servers']):
            print('Already saved; use its existing entry.')
            continue
        name = input('Display name: ').strip()
        if not name or len(name) > 100 or any(not c.isprintable() for c in name) or any(s['name'].casefold() == name.casefold() for s in settings['servers']):
            raise ValueError('Use a unique display name of 1–100 printable characters')
        port = int(input('SSH port [22]: ').strip() or '22')
        if not 1 <= port <= 65535:
            raise ValueError('Invalid SSH port')
        is_self = host in self_addresses() or input('Is this the n8n host? [y/N]: ').strip().lower() == 'y'
        password = getpass.getpass('Root SSH password (hidden): ')
        if not password:
            raise ValueError('Root SSH password is required')
        sid = secrets.token_hex(6)
        server = {'id': sid, 'host': host, 'port': port, 'name': name,
                  'credential_id': 'WeeklyVps_' + sid, 'credential_name': 'Weekly VPS ' + name,
                  'is_n8n_host': is_self, 'reboot_ready': False}
        data = {'host': host, 'port': port, 'username': 'root', 'password': password}
        import_value([{'id':server['credential_id'], 'name':server['credential_name'], 'type':'sshPassword', 'data':data}], 'credentials')
        settings['servers'].append(server)
        if is_self:
            settings.setdefault('protected_hosts', []).append(host)
        save(settings)
        print('Saved: ' + name + (' (n8n host: update only, no Telegram reboot)' if is_self else ''))


def build_workflow(servers):
    template = json.loads(Path('workflows/vps-weekly-updates.json').read_text())
    workflow = {**template, 'active':False, 'nodes':template['nodes'][:2], 'connections':{},
                'settings':{**template['settings'], 'saveExecutionProgress':True}}
    # Running the host last reduces interruption to other maintenance work.
    ordered = sorted(servers, key=lambda s: bool(s.get('is_n8n_host')))
    results = []
    def link(a, b): workflow['connections'][a] = {'main':[[{'node':b, 'type':'main', 'index':0}]]}
    previous = None
    for i, server in enumerate(ordered):
        node_name, ssh_name, result_name = 'VPS name '+server['id'], 'Update '+server['id'], 'Result '+server['id']
        x = 240+i*720
        workflow['nodes'].append({'id':'name-'+server['id'], 'name':node_name, 'type':'n8n-nodes-base.code', 'typeVersion':2,
            'position':[x,0], 'parameters':{'jsCode':'return [{json: {vps_name: '+json.dumps(server['name'])+'}}];'}})
        ssh = json.loads(json.dumps(template['nodes'][3]))
        ssh.update(id='ssh-'+server['id'], name=ssh_name, position=[x+240,0], onError='continueRegularOutput')
        ssh['parameters']['command'] = ssh['parameters']['command'].replace('export DEBIAN_FRONTEND=',
            "[ ! -f /run/n8n-reboot-pending ] || { echo 'A reboot is pending' >&2; exit 1; }\nexport DEBIAN_FRONTEND=")
        ssh['credentials'] = {'sshPassword':{'id':server['credential_id'], 'name':server['credential_name']}}
        workflow['nodes'].append(ssh)
        code = """const r = $input.first().json;
const markers = String(r.stdout || '').match(/^N8N_REBOOT_REQUIRED=([01])$/gm) || [];
const ok = r.code === 0 && markers.length === 1;
return [{json:{maintenance_version:1, vps_name:NAME, update_status:ok?'success':'failed',
reboot_required:ok && markers[0].endsWith('1')}}];""".replace('NAME', json.dumps(server['name']))
        workflow['nodes'].append({'id':'result-'+server['id'], 'name':result_name,'type':'n8n-nodes-base.code','typeVersion':2,
            'position':[x+480,0], 'parameters':{'jsCode':code}})
        link(node_name, ssh_name); link(ssh_name, result_name)
        if previous: link(previous, node_name)
        else:
            link(template['nodes'][0]['name'], node_name); link(template['nodes'][1]['name'], node_name)
        previous = result_name
        results.append(result_name)
    summary = {'id':'summary','name':'VPS reboot summary','type':'n8n-nodes-base.code','typeVersion':2,
        'position':[240+len(ordered)*720,0], 'parameters':{'jsCode':'return ['+','.join('$('+json.dumps(n)+').first()' for n in results)+'];'}}
    workflow['nodes'].append(summary); link(previous, summary['name'])
    check = {'id':'check','name':'Any update failed?','type':'n8n-nodes-base.if','typeVersion':2.2,'position':[summary['position'][0]+240,0],
        'parameters':{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},
        'conditions':[{'id':'failed','leftValue':"={{ $('VPS reboot summary').all().some(i => i.json.update_status !== 'success') }}",'rightValue':True,
        'operator':{'type':'boolean','operation':'true','singleValue':True}}], 'combinator':'and'},'options':{}}}
    workflow['nodes'].append(check); link(summary['name'], check['name'])
    workflow['nodes'].append({'id':'failed','name':'Review VPS updates','type':'n8n-nodes-base.stopAndError','typeVersion':1,
        'position':[check['position'][0]+240,-100],'parameters':{'errorMessage':'One or more VPS updates failed. Inspect each SSH/result node; other servers were still attempted.'}})
    workflow['connections'][check['name']] = {'main':[[{'node':'Review VPS updates','type':'main','index':0}],[]]}
    # Preserve the native daily/weekly schedule settings in the existing template.
    return workflow


def main():
    os.umask(0o077)
    try:
        settings = load()
        migrate(settings)
        add(settings)
        if not settings['servers']:
            raise ValueError('No VPS servers saved')
        for server in settings['servers']:
            if protected(server, settings):
                server['is_n8n_host'] = True
                server['reboot_ready'] = False
            elif not server.get('reboot_ready') or not (KEYS/server['id']).is_file() or not (KEYS/(server['id']+'.known_hosts')).is_file():
                setup_key(server, credential(server['credential_id']))
            save(settings)
        settings['telegram_reboot_enabled'] = True
        save(settings)
        import_value(build_workflow(settings['servers']), 'workflow')
        print('Imported INACTIVE: VPS weekly updates. Publish it again for Mondays 00:00 UK.')
        print('Saved display names are used in Telegram. Send reboot to select one or more eligible servers.')
        print('The n8n host updates last and cannot be rebooted through Telegram. No upgrades or reboots were run by setup.')
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, StopIteration):
        raise SystemExit('VPS setup could not finish. Check services/SSH access and run vps-manage again; saved inventory is kept. No secret values printed.') from None


if __name__ == '__main__':
    main()
