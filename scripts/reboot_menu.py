"""Owner-only Telegram multi-select reboot controls with expiring one-shot menus."""
import fcntl
import json
import re
import secrets
import time

import vps
from telegram_errors import TelegramError


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS bot_health (id INTEGER PRIMARY KEY CHECK(id=1), last_poll REAL NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '');
      INSERT OR IGNORE INTO bot_health(id) VALUES (1);
      CREATE TABLE IF NOT EXISTS bot_cursor (id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL);
      INSERT OR IGNORE INTO bot_cursor VALUES (1,0);
      CREATE TABLE IF NOT EXISTS reboot_menus (
        nonce TEXT PRIMARY KEY, chat_id TEXT NOT NULL, message_id INTEGER,
        selected TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS reboot_requests (
        server_id TEXT PRIMARY KEY, requested_at REAL NOT NULL);
    ''')
    if 'choices' not in {r[1] for r in db.execute('PRAGMA table_info(reboot_menus)')}:
        db.execute("ALTER TABLE reboot_menus ADD COLUMN choices TEXT NOT NULL DEFAULT '[]'")
        db.commit()


def owner(message, user, settings):
    chat = message.get('chat', {})
    return (chat.get('type') == 'private' and str(chat.get('id')) == str(settings.get('chat_id'))
            and str(user.get('id')) == str(settings.get('chat_id')) and not user.get('is_bot', False))


def process(db, settings, update, api, now=None):
    now = time.time() if now is None else now
    message = update.get('message')
    # Old inline keyboards are inert. All control now uses ordinary messages.
    if not message or not owner(message, message.get('from', {}), settings): return
    if message.get('forward_origin') or message.get('forward_date') or message.get('via_bot'): return
    if not now - 120 <= message.get('date', 0) <= now + 30: return
    text = message.get('text', '').strip().casefold()
    command = re.fullmatch(r'/?reboot(?:@([a-z0-9_]+))?', text)
    selection = re.fullmatch(r'[0-9]+(?:\s*,\s*[0-9]+)*', text) if len(text) <= 512 else None
    cancel = text in {'cancel', '/cancel'}
    if not command and not selection and not cancel: return
    if command and command.group(1) and command.group(1) != settings.get('bot_username', '').casefold(): return
    inventory = vps.load()
    def send(text):
        return api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'], 'text':text})
    if not inventory.get('telegram_reboot_enabled', False):
        send('Reboot controls are OFF or setup did not finish. Run sudo vps-manage on the n8n VPS, or enable option r in sudo telegram. No servers were rebooted.')
        return
    if command:
        servers = vps.eligible(inventory)
        if not servers:
            send('No eligible servers. Run sudo vps-manage on the n8n VPS. The n8n host cannot be rebooted here.')
            return
        nonce = secrets.token_hex(12)
        db.execute("UPDATE reboot_menus SET state='expired' WHERE chat_id=? AND state='open'", (str(settings['chat_id']),))
        db.execute('INSERT INTO reboot_menus(nonce,chat_id,message_id,selected,expires,state,choices) VALUES (?,?,NULL,?,?,?,?)',
                   (nonce, str(settings['chat_id']), '[]', now+300, 'open', json.dumps([s['id'] for s in servers])))
        db.commit()
        lines = [str(i)+'. '+s['name'] for i,s in enumerate(servers,1)]
        response = send('Choose servers to reboot:\n'+'\n'.join(lines)+
            '\n\nReply 1, 2, or 1,2 to reboot those servers, or cancel. Reboots are scheduled in 1 minute. '
            'This list expires in 5 minutes. The n8n host is excluded.')
        db.execute('UPDATE reboot_menus SET message_id=? WHERE nonce=?', (response['message_id'], nonce))
        db.commit()
        return
    row = db.execute("SELECT * FROM reboot_menus WHERE chat_id=? AND state='open' ORDER BY rowid DESC LIMIT 1",
                     (str(settings['chat_id']),)).fetchone()
    if not row or row['expires'] <= now or not row['message_id'] or not json.loads(row['choices']):
        send('No active server list. Send reboot for a fresh list. No servers were rebooted.')
        return
    # Never apply queued messages from before the prompt or replies to an older list.
    if message.get('message_id', 0) <= row['message_id']: return
    reply = message.get('reply_to_message')
    if reply and reply.get('message_id') != row['message_id']:
        send('Reply to the latest server list or send reboot for a fresh list. No servers were rebooted.')
        return
    if cancel:
        db.execute("UPDATE reboot_menus SET state='cancelled' WHERE nonce=?", (row['nonce'],))
        db.commit()
        send('Cancelled. No servers were rebooted.')
        return
    choices = json.loads(row['choices'])
    numbers = set(int(n.strip()) for n in text.split(','))
    if not numbers or any(n < 1 or n > len(choices) for n in numbers):
        send('Use only numbers from this list, separated by commas, or cancel. No servers were rebooted.')
        return
    selected = [choices[n-1] for n in sorted(numbers)]
    allowed = {s['id']:s for s in vps.eligible(inventory)}
    if any(sid not in allowed for sid in selected):
        send('A selected server is now protected or unavailable. Send reboot for a fresh list. No servers were rebooted.')
        return
    recent = {r['server_id'] for r in db.execute('SELECT server_id FROM reboot_requests WHERE requested_at>?', (now-180,))}
    # Consume before network actions, with persistent cooldowns. Never replay an
    # ambiguous request after a crash or a failed Telegram acknowledgement.
    changed = db.execute("UPDATE reboot_menus SET state='dispatched', selected=? WHERE nonce=? AND state='open'",
                         (json.dumps(selected), row['nonce'])).rowcount
    if not changed: return
    for sid in selected:
        if sid not in recent:
            db.execute('INSERT OR REPLACE INTO reboot_requests VALUES (?,?)', (sid, now))
    db.commit()
    send('Received. Processing reboot requests for: '+', '.join(allowed[sid]['name'] for sid in selected)+'.')
    lines = []
    for sid in selected:
        name = allowed[sid]['name']
        if sid in recent:
            lines.append(name+': skipped, a reboot was recently requested.')
            continue
        try:
            vps.reboot(sid)  # Independently rechecks host protection at dispatch.
            lines.append(name+': reboot scheduled in 1 minute.')
        except Exception:
            lines.append(name+': reboot could not be confirmed. Check the server before trying again.')
    send('\n'.join(lines))

def tick(db, settings, api, lock_path, now=None):
    if not settings.get('token') or not settings.get('chat_id'):
        return
    init(db)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return  # Interactive bot setup owns getUpdates temporarily.
        offset = db.execute('SELECT offset FROM bot_cursor WHERE id=1').fetchone()[0]
        updates = api(settings['token'], 'getUpdates', {'offset':offset, 'timeout':20, 'limit':100,
            'allowed_updates':['message']})
        db.execute("UPDATE bot_health SET last_poll=?, error_code='' WHERE id=1", (time.time() if now is None else now,))
        db.commit()
        for update in updates:
            if update['update_id'] < offset: continue
            # A persistent cursor prevents old commands being replayed after restart.
            offset = update['update_id'] + 1
            db.execute('UPDATE bot_cursor SET offset=? WHERE id=1', (offset,))
            db.commit()
            try:
                process(db, settings, update, api, now)
            except Exception as error:
                # A rejected message response must not discard the rest of this
                # batch or pause all buttons for the alert retry interval.
                if not isinstance(error, TelegramError):
                    raise
                db.rollback()
                print('Telegram update response failed (method=' + error.method +
                      ' code=' + str(error.code) + ' reason=' + error.reason + ').', flush=True)
                if error.code == 429:
                    raise
