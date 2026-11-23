"""Opt-in Telegram alerts for saved n8n failures and VPS update results. Standard library only.

n8n is queried in read-only transactions. Tokens never enter command arguments,
workflow exports, error messages or the public repository.
"""
import fcntl
from contextlib import closing
import getpass
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import threading
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CONFIG = Path('config/telegram.json')
STATE = Path('data/telegram.sqlite3')
POLL_SECONDS = 20


from telegram_errors import TelegramError


def api_error(method, code, body):
    # Classify known API descriptions; never log raw responses, IDs or URLs.
    description = str(body.get('description', '')).casefold()
    reason = 'REQUEST_REJECTED'
    for phrase, category in (
        ('query is too old', 'CALLBACK_EXPIRED'),
        ('query id is invalid', 'CALLBACK_EXPIRED'),
        ('message is not modified', 'MESSAGE_UNCHANGED'),
        ('message to edit not found', 'MESSAGE_NOT_FOUND'),
        ("message can't be edited", 'MESSAGE_NOT_EDITABLE'),
        ('chat not found', 'CHAT_NOT_FOUND'),
    ):
        if phrase in description:
            reason = category
            break
    return TelegramError(code, body.get('parameters', {}).get('retry_after', 60), method, reason)


def api(token, method, payload=None):
    if method not in {'getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage', 'answerCallbackQuery', 'editMessageReplyMarkup'}:
        raise ValueError('Unsupported Telegram method')
    request = Request(f'https://api.telegram.org/bot{token}/{method}',
                      data=json.dumps(payload or {}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=30 if method == 'getUpdates' else 20) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            body = json.loads(exc.read(65536))
        except (ValueError, OSError):
            body = {}
        raise api_error(method, exc.code, body) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise TelegramError(method=method, reason='TRANSPORT') from None
    if not result.get('ok'):
        raise api_error(method, result.get('error_code', 0), result)
    return result.get('result')


def config():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def save_config(settings):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(settings, stream)
        stream.write('\n')
    temporary.replace(CONFIG)


def database():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(STATE, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    os.chmod(STATE, 0o600)
    db = sqlite3.connect(STATE, timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS delivery_control (id INTEGER PRIMARY KEY CHECK(id=1), next_request REAL NOT NULL);
        INSERT OR IGNORE INTO delivery_control VALUES (1,0);
        CREATE TABLE IF NOT EXISTS subscriptions (
            workflow_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
            enabled_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries (
            execution_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0);
    ''')
    return db


def sql(query):
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql',
                             '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'n8n', '-d', 'n8n'],
                            input='BEGIN READ ONLY; SET LOCAL statement_timeout=5000;\n' + query + '\nCOMMIT;',
                            text=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    return json.loads(result.stdout.strip())


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def verify_schema():
    rows = sql("SELECT coalesce(json_agg(json_build_object('table',table_name,'column',column_name)), '[]') FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('execution_entity','workflow_entity');")
    required = {'execution_entity': {'id', 'workflowId', 'createdAt', 'startedAt', 'stoppedAt', 'status', 'deletedAt'},
                'workflow_entity': {'id', 'name'}}
    if any(not columns <= {r['column'] for r in rows if r['table'] == table}
           for table, columns in required.items()):
        raise RuntimeError('n8n execution schema differs. Telegram monitoring needs review.')


def workflows():
    return sql('SELECT coalesce(json_agg(r), \'[]\') FROM (SELECT id, name FROM workflow_entity ORDER BY name,id) r;')


def set_subscription(db, workflow_id, enabled):
    # Use the database clock to match execution timestamps; do not replay history.
    since = sql("SELECT to_json(now());")
    db.execute('INSERT INTO subscriptions VALUES (?, ?, ?) ON CONFLICT(workflow_id) DO UPDATE SET enabled=excluded.enabled, enabled_at=excluded.enabled_at',
               (workflow_id, int(enabled), since))
    db.commit()


def failed_executions(subscriptions):
    if not subscriptions:
        return []
    scopes = ' OR '.join('(e."workflowId"=' + literal(row['workflow_id']) +
                         ' AND e."createdAt">=' + literal(row['enabled_at']) + '::timestamptz)' for row in subscriptions)
    return sql('''SELECT coalesce(json_agg(r), '[]') FROM (
        SELECT e.id::text AS id, e."workflowId" AS workflow_id, w.name,
               e.status, e."stoppedAt" AS stopped_at, e."startedAt" AS started_at
        FROM execution_entity e JOIN workflow_entity w ON w.id=e."workflowId"
        WHERE e.status IN ('error','crashed') AND e."deletedAt" IS NULL AND (''' + scopes + ''')
        ORDER BY e.id) r;''')


MAINTENANCE_WORKFLOW = 'VpsWeeklyUpdatesV1'


def maintenance_results(serialized):
    """Read only the fixed summary node from n8n's flatted execution graph.

    References are followed one level per path step, not recursively: cyclic
    graphs and numeric strings in the string table cannot confuse decoding.
    """
    if len(serialized) > 4_000_000:
        raise ValueError('Maintenance execution exceeds size limit')
    table = json.loads(serialized)
    if not isinstance(table, list) or not table:
        raise ValueError('Invalid execution graph')
    def ref(value):
        if isinstance(value, str):
            if not value.isdecimal() or int(value) >= len(table):
                raise ValueError('Invalid execution reference')
            return table[int(value)]
        return value
    def field(value, key):
        return ref(value[key])
    root = table[0]
    run_data = field(field(root, 'resultData'), 'runData')
    if 'VPS reboot summary' in run_data:
        runs = field(run_data, 'VPS reboot summary')
    else:
        # Host/Docker upgrades can interrupt the final summary; saved progress
        # retains earlier completed per-server results for the same workflow.
        runs = [run for name, raw in run_data.items()
                if re.fullmatch(r'Result [a-f0-9]{12}', name) for run in ref(raw)]
    results = []
    for raw_run in runs:
        run = ref(raw_run)
        if 'error' in run:
            continue
        outputs = field(field(run, 'data'), 'main')
        for raw_output in outputs:
            for raw_item in ref(raw_output):
                data = field(ref(raw_item), 'json')
                if field(data, 'maintenance_version') != 1:
                    raise ValueError('Invalid maintenance summary')
                status = field(data, 'update_status')
                if status == 'failed':
                    continue
                if status != 'success':
                    raise ValueError('Invalid maintenance status')
                name = field(data, 'vps_name')
                reboot = field(data, 'reboot_required')
                if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(not c.isprintable() for c in name):
                    raise ValueError('Invalid VPS name')
                if type(reboot) is not bool:
                    raise ValueError('Invalid reboot status')
                if not any(item['name'] == name for item in results):
                    results.append({'name':name, 'reboot_required':reboot})
    return results


def reboot_names(serialized):
    return [item['name'] for item in maintenance_results(serialized) if item['reboot_required']]


def maintenance_text(results):
    lines = []
    for item in results:
        name = item['name']
        reboot = 'reboot required' if item['reboot_required'] else 'no reboot required'
        lines.append(name+' updated successfully ✅ '+reboot)
    return '\n'.join(lines)


def reboot_executions(subscriptions):
    scope = next((s for s in subscriptions if s['workflow_id'] == MAINTENANCE_WORKFLOW), None)
    if scope is None:
        return []
    # No school bodies or SSH credentials are queried. Only this workflow's
    # successful saved outputs are eligible, after the opt-in timestamp.
    rows = sql('''SELECT coalesce(json_agg(r), '[]') FROM (
        SELECT e.id::text AS id, e."workflowId" AS workflow_id, d.data
        FROM execution_entity e JOIN execution_data d ON d."executionId"=e.id
        WHERE e."workflowId"=''' + literal(MAINTENANCE_WORKFLOW) + '''
          AND e.status IN ('success','error','crashed') AND e."deletedAt" IS NULL
          AND e."createdAt">=''' + literal(scope['enabled_at']) + '''::timestamptz
        ORDER BY e.id) r;''')
    result = []
    for row in rows:
        try:
            results = maintenance_results(row['data'])
        except (ValueError, KeyError, TypeError, IndexError):
            # A deleted/edited summary must not suppress other workflow alerts.
            print('Maintenance summary unavailable; inspect its execution.', flush=True)
            continue
        if results:
            # Keep the delivery identity so existing reboot alerts are not repeated.
            result.append({'id': 'reboot:' + row['id'], 'workflow_id': row['workflow_id'], 'maintenance_results': results})
    return result


def public_url():
    values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    url = values.get('N8N_PUBLIC_URL', '').rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Invalid n8n editor URL')
    return url


def alert_payload(row, chat_id, base_url):
    name = ''.join(c for c in row['name'] if c.isprintable())[:200]
    text = '❌ <b>Workflow failed</b>\n\n<b>' + html.escape(name) + '</b>'
    text += '\nStatus: ' + ('Crashed' if row['status'] == 'crashed' else 'Failed')
    stamp = row.get('stopped_at') or row.get('started_at')
    if stamp:
        local = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/London'))
        text += '\nTime: ' + local.strftime('%d %b %Y, %H:%M %Z')
    text += '\nExecution: #' + html.escape(str(row['id']))
    url = base_url + '/workflow/' + quote(row['workflow_id'], safe='') + '/executions/' + quote(str(row['id']), safe='')
    return {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
            'link_preview_options': {'is_disabled': True},
            'reply_markup': {'inline_keyboard': [[{'text': 'Open failed execution', 'url': url}]]}}


def tick(db, settings, now=None):
    if not settings.get('token') or not settings.get('chat_id'):
        return 0
    subscriptions = db.execute('SELECT * FROM subscriptions WHERE enabled=1').fetchall()
    if not subscriptions:
        return 0
    now = time.time() if now is None else now
    # Persistent cooldown also covers restarting during a Telegram outage/rate limit.
    if db.execute('SELECT next_request FROM delivery_control WHERE id=1').fetchone()[0] > now:
        return 0
    sent_count = 0
    base_url = public_url()
    for row in failed_executions(subscriptions) + reboot_executions(subscriptions):
        # Re-check opt-in before sending in case the helper disabled it mid-poll.
        subscription = db.execute('SELECT enabled, enabled_at FROM subscriptions WHERE workflow_id=?', (row['workflow_id'],)).fetchone()
        original = next(s for s in subscriptions if s['workflow_id'] == row['workflow_id'])
        if not subscription or not subscription['enabled'] or subscription['enabled_at'] != original['enabled_at']:
            continue
        db.execute('INSERT OR IGNORE INTO deliveries(execution_id,workflow_id) VALUES (?,?)', (row['id'], row['workflow_id']))
        delivery = db.execute('SELECT * FROM deliveries WHERE execution_id=?', (row['id'],)).fetchone()
        if delivery['sent'] or delivery['next_attempt'] > now:
            continue
        attempts = delivery['attempts'] + 1
        # Persist the lease before network I/O; interrupted sends retry after it expires.
        db.execute('UPDATE deliveries SET attempts=?,next_attempt=? WHERE execution_id=?', (attempts, now + 60, row['id']))
        db.commit()
        try:
            payload = ({'chat_id': settings['chat_id'], 'text': maintenance_text(row['maintenance_results'])}
                       if 'maintenance_results' in row else alert_payload(row, settings['chat_id'], base_url))
            api(settings['token'], 'sendMessage', payload)
        except TelegramError as exc:
            delay = max(exc.retry_after, min(3600, 20 * 2 ** min(attempts, 7)))
            db.execute('UPDATE deliveries SET next_attempt=? WHERE execution_id=?', (now + delay, row['id']))
            db.execute('UPDATE delivery_control SET next_request=? WHERE id=1', (now + delay,))
            db.commit()
            # Do not hammer a rate-limited, revoked or unavailable bot for other runs.
            print(f'Telegram delivery pending (code {exc.code}); will retry.', flush=True)
            break
        db.execute('UPDATE deliveries SET sent=1 WHERE execution_id=?', (row['id'],))
        db.commit()
        sent_count += 1
        if sent_count >= 10:
            break
        time.sleep(0.1)
    db.commit()
    return sent_count


def setup():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent/'telegram-updates.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return setup_locked()


def setup_locked():
    old = config()
    token = getpass.getpass('Telegram bot token' + (' [Enter keeps saved token]' if old.get('token') else '') + ': ').strip() or old.get('token', '')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token):
        raise SystemExit('Invalid token format. Nothing changed.')
    print('Token received: ' + token[:4] + '…' + token[-4:])
    bot = api(token, 'getMe')
    if api(token, 'getWebhookInfo').get('url'):
        raise SystemExit('This bot has a webhook configured. Use a dedicated alert bot; no settings changed.')
    nonce = 'n8n_' + secrets.token_hex(12)
    username = bot.get('username', '')
    if not re.fullmatch(r'[A-Za-z0-9_]+', username):
        raise SystemExit('Telegram returned an invalid bot username. Nothing saved.')
    print(f'Open https://t.me/{username}?start={nonce} and press START in your private chat.')
    print('This one-time link identifies your chat. No workflow alerts are enabled by setup.')
    offset = None
    while True:
        if input('After pressing Start, press Enter here (q cancels): ').strip().lower() == 'q':
            raise SystemExit('Cancelled. Saved settings unchanged.')
        for _ in range(5):
            payload = {'timeout': 0, 'limit': 100, 'allowed_updates': ['message']}
            if offset is not None:
                payload['offset'] = offset
            updates = api(token, 'getUpdates', payload)
            for update in updates:
                offset = max(offset or 0, update['update_id'] + 1)
                message = update.get('message', {})
                chat = message.get('chat', {})
                if chat.get('type') == 'private' and message.get('text') == '/start ' + nonce:
                    settings = {'token': token, 'chat_id': str(chat['id']), 'bot_username': username}
                    api(token, 'sendMessage', {'chat_id': settings['chat_id'], 'text': '✅ n8n alerts connected.\n\nNo workflows were enabled automatically. Run telegram on your VPS to choose one.'})
                    save_config(settings)
                    with closing(database()) as db:
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_cursor'").fetchone():
                            db.execute('UPDATE bot_cursor SET offset=? WHERE id=1', (offset or 0,))
                            db.execute("UPDATE reboot_menus SET state='expired' WHERE state='open'")
                        db.execute('UPDATE delivery_control SET next_request=0 WHERE id=1')
                        db.execute('UPDATE deliveries SET next_attempt=0 WHERE sent=0')
                        db.commit()
                    print('Telegram connected and test message delivered. Token saved only on this VPS.')
                    return
            if len(updates) < 100:
                break
        print('Matching Start message not found yet. Open the exact link above, then try again.')


def helper():
    if not config().get('chat_id'):
        setup()
    verify_schema()
    while True:
        with closing(database()) as db:
            subscriptions = {r['workflow_id']: r for r in db.execute('SELECT * FROM subscriptions')}
        rows = workflows()
        print('\nTelegram alerts (failures; VPS maintenance also reports successful updates and reboot requirements)\n')
        for number, row in enumerate(rows, 1):
            enabled = row['id'] in subscriptions and subscriptions[row['id']]['enabled']
            print(f"{number}) {row['name']} | {'ON' if enabled else 'OFF'}")
        if not rows:
            print('No workflows yet. Import/create one first; none will be enabled automatically.')
        print('s) Change bot token / reconnect chat\nt) Send a test message\nr) Toggle Telegram reboot controls\n0) Exit')
        choice = input('Workflow number to toggle alerts: ').strip().lower()
        if choice in ('', '0'):
            return
        if choice == 's':
            setup()
        elif choice == 'r':
            import vps
            inventory = vps.load()
            inventory['telegram_reboot_enabled'] = not inventory.get('telegram_reboot_enabled', False)
            vps.save(inventory)
            print('Telegram reboot controls ' + ('ON' if inventory['telegram_reboot_enabled'] else 'OFF') + '. The n8n host is always excluded.')
        elif choice == 't':
            saved = config()
            api(saved['token'], 'sendMessage', {'chat_id': saved['chat_id'], 'text': '✅ n8n Telegram test. Failure alerts follow your selected workflows.'})
            print('Test delivered.')
        elif choice.isdigit() and 1 <= int(choice) <= len(rows):
            row = rows[int(choice) - 1]
            enabled = not (row['id'] in subscriptions and subscriptions[row['id']]['enabled'])
            with closing(database()) as db:
                set_subscription(db, row['id'], enabled)
            print(f"Alerts {'enabled' if enabled else 'disabled'} for: {row['name']}")
            print('Only new executions are eligible. No republish needed.')
        else:
            print('Choose a listed option.')


def command_monitor():
    """Commands must not wait for PostgreSQL, Docker or failure-alert delivery."""
    import reboot_menu
    while True:
        delay = 0
        try:
            settings = config()
            if not settings.get('token') or not settings.get('chat_id'):
                delay = POLL_SECONDS
            with closing(database()) as db:
                reboot_menu.tick(db, settings, api, STATE.parent/'telegram-updates.lock')
        except Exception as exc:
            code = str(exc.code) if isinstance(exc, TelegramError) else type(exc).__name__
            detail = (' method=' + exc.method + ' reason=' + exc.reason) if isinstance(exc, TelegramError) else ''
            print('Telegram command polling unavailable (code ' + code + detail + '); retrying.', flush=True)
            try:
                with closing(database()) as db:
                    reboot_menu.init(db)
                    db.execute('UPDATE bot_health SET error_code=? WHERE id=1', (code,))
                    db.commit()
            except Exception:
                pass
            delay = max(2, exc.retry_after) if isinstance(exc, TelegramError) and exc.code == 429 else 2
        time.sleep(delay)


def online_monitor():
    """SSH boot checks run separately so Telegram commands stay responsive."""
    import reboot_menu
    while True:
        try:
            with closing(database()) as db:
                reboot_menu.check_online(db, config(), api)
        except Exception as error:
            print('Reboot readiness monitoring unavailable ('+type(error).__name__+'); retrying.', flush=True)
        time.sleep(5)


def status():
    import reboot_menu, vps
    settings = config()
    inventory = vps.load()
    print('Bot: @' + settings.get('bot_username', 'not recorded'))
    print('Telegram chat configured: ' + ('YES' if settings.get('token') and settings.get('chat_id') else 'NO'))
    print('Reboot controls: ' + ('ON' if inventory.get('telegram_reboot_enabled') else 'OFF (finish sudo vps-manage, or enable option r in sudo telegram)'))
    for server in inventory.get('servers', []):
        mode = 'n8n HOST: REBOOT BLOCKED' if vps.protected(server, inventory) else ('reboot key ready' if server.get('reboot_ready') and server.get('reboot_key_version') == vps.KEY_VERSION and (vps.KEYS/server['id']).is_file() and (vps.KEYS/(server['id']+'.known_hosts')).is_file() else 'reboot key NOT READY: run sudo vps-manage')
        print(server['name'] + ' | ' + server['host'] + ' | ' + mode)
    with closing(database()) as db:
        reboot_menu.init(db)
        subscription = db.execute('SELECT enabled FROM subscriptions WHERE workflow_id=?', (MAINTENANCE_WORKFLOW,)).fetchone()
        print('VPS update Telegram alerts: ' + ('ON' if subscription and subscription['enabled'] else 'OFF (enable VPS weekly updates in sudo telegram)'))
        row = db.execute('SELECT * FROM bot_health WHERE id=1').fetchone()
        print('Last command poll: ' + (str(round(max(0, time.time()-row['last_poll']))) + ' seconds ago' if row['last_poll'] else 'not recorded yet'))
        if row['error_code']:
            print('Last polling error code: ' + row['error_code'])
            if row['error_code'] == '409':
                print('Check whether another service is using getUpdates or a webhook for the same bot.')
    print('Send a NEW reboot message in the bound private bot chat. Old commands are not replayed.')


def monitor():
    lock = Path('data/telegram-monitor.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Thread(target=command_monitor, name='telegram-commands', daemon=True).start()
    threading.Thread(target=online_monitor, name='telegram-reboot-readiness', daemon=True).start()
    verified = False
    while True:
        try:
            settings = config()
            with closing(database()) as db:
                if settings.get('chat_id') and db.execute('SELECT 1 FROM subscriptions WHERE enabled=1 LIMIT 1').fetchone():
                    if not verified:
                        verify_schema()
                        verified = True
                    tick(db, settings)
        except Exception as exc:
            # Never print exception text: an HTTP exception can contain the token URL.
            print(f'Telegram alerts unavailable ({type(exc).__name__}); retrying.', flush=True)
        time.sleep(POLL_SECONDS)


def main():
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv) > 1 else 'helper'
    try:
        if mode == 'monitor':
            monitor()
        elif mode == 'setup':
            if input('Connect Telegram failure alerts now? [y/N]: ').strip().lower() == 'y':
                setup()
        elif mode == 'status':
            status()
        elif mode == 'helper':
            helper()
        else:
            raise SystemExit('Unknown Telegram helper action.')
    except KeyboardInterrupt:
        raise SystemExit('\nCancelled.') from None
    except TelegramError as exc:
        raise SystemExit(str(exc) + ' Check the token, bot chat and connectivity; run telegram again.') from None
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        raise SystemExit(f'Telegram helper failed ({type(exc).__name__}). Check services and run telegram again.') from None


if __name__ == '__main__':
    main()
