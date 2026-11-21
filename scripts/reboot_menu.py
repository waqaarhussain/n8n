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


def buttons(nonce, servers, selected):
    rows = [[{'text': ('☑ ' if s['id'] in selected else '☐ ') + s['name'],
              'callback_data': 'rb:' + nonce + ':pick:' + s['id']}] for s in servers]
    rows.append([{'text':'Reboot selected', 'callback_data':'rb:'+nonce+':go'},
                 {'text':'Cancel', 'callback_data':'rb:'+nonce+':cancel'}])
    return {'inline_keyboard': rows}


def owner(message, user, settings):
    chat = message.get('chat', {})
    return (chat.get('type') == 'private' and str(chat.get('id')) == str(settings.get('chat_id'))
            and str(user.get('id')) == str(settings.get('chat_id')) and not user.get('is_bot', False))


def process(db, settings, update, api, now=None):
    now = time.time() if now is None else now
    inventory = vps.load()
    enabled = inventory.get('telegram_reboot_enabled', False)
    message = update.get('message')
    if message:
        if not owner(message, message.get('from', {}), settings): return
        if message.get('forward_origin') or message.get('forward_date') or message.get('via_bot'): return
        if not now - 120 <= message.get('date', 0) <= now + 30: return
        text = message.get('text', '').strip().casefold()
        command = re.fullmatch(r'/?reboot(?:@([a-z0-9_]+))?', text)
        if not command or (command.group(1) and command.group(1) != settings.get('bot_username', '').casefold()): return
        if not enabled:
            api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'], 'text':'Reboot controls are OFF or setup did not finish. Run sudo vps-manage on the n8n VPS, or enable option r in sudo telegram. No servers were rebooted.'})
            return
        servers = vps.eligible(inventory)
        if not servers:
            api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'], 'text':'No eligible servers. Run sudo vps-manage on the n8n VPS. The n8n host cannot be rebooted here.'})
            return
        nonce = secrets.token_hex(12)
        # Only the newest menu is live; buttons from old messages become harmless.
        db.execute("UPDATE reboot_menus SET state='expired' WHERE chat_id=? AND state='open'", (str(settings['chat_id']),))
        db.execute('INSERT INTO reboot_menus VALUES (?,?,NULL,?,?,?)', (nonce, str(settings['chat_id']), '[]', now+300, 'open'))
        db.commit()
        response = api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'],
            'text':'Select servers to reboot, then tap Reboot selected. Reboots are scheduled in 1 minute.\nThe n8n host is excluded.',
            'reply_markup':buttons(nonce, servers, [])})
        db.execute('UPDATE reboot_menus SET message_id=? WHERE nonce=?', (response['message_id'], nonce))
        db.commit()
        return
    callback = update.get('callback_query', {})
    if not callback or not owner(callback.get('message', {}), callback.get('from', {}), settings): return
    if not enabled:
        api(settings['token'], 'answerCallbackQuery', {'callback_query_id':callback['id'], 'text':'Reboot controls are OFF. No action taken.'})
        return
    data = callback.get('data', '')
    match = re.fullmatch(r'rb:([a-f0-9]{24}):(go|cancel|pick:([a-f0-9]{12}))', data)
    if not match: return
    nonce, action, sid = match.group(1), match.group(2), match.group(3)
    row = db.execute('SELECT * FROM reboot_menus WHERE nonce=? AND chat_id=?', (nonce, str(settings['chat_id']))).fetchone()
    def answer(text):
        try:
            api(settings['token'], 'answerCallbackQuery', {'callback_query_id':callback['id'], 'text':text})
            return True
        except Exception as error:
            # An expired toast must not block checkbox rendering or cancellation.
            if getattr(error, 'code', None) == 400 and getattr(error, 'reason', '') == 'CALLBACK_EXPIRED':
                return False
            raise
    if not row or row['state'] != 'open' or row['expires'] <= now or row['message_id'] != callback['message'].get('message_id'):
        answer('Menu expired or already used. Send reboot for a new menu.')
        return
    servers = vps.eligible(inventory)
    allowed = {s['id']:s for s in servers}
    selected = set(json.loads(row['selected'])) & allowed.keys()
    if action.startswith('pick:'):
        if sid not in allowed:
            answer('That server is protected or unavailable.')
            return
        selected.symmetric_difference_update({sid})
        db.execute('UPDATE reboot_menus SET selected=? WHERE nonce=?', (json.dumps(sorted(selected)), nonce))
        db.commit()
        answer('Selection updated')
        api(settings['token'], 'editMessageReplyMarkup', {'chat_id':settings['chat_id'], 'message_id':row['message_id'],
            'reply_markup':buttons(nonce, servers, selected)})
        return
    if action == 'cancel':
        db.execute("UPDATE reboot_menus SET state='cancelled' WHERE nonce=?", (nonce,))
        db.commit(); answer('Cancelled')
        api(settings['token'], 'editMessageReplyMarkup', {'chat_id':settings['chat_id'], 'message_id':row['message_id'], 'reply_markup':{'inline_keyboard':[]}})
        return
    if not selected:
        answer('Select at least one eligible server first.')
        return
    # A stale click cannot consume the menu, reserve a cooldown, or reboot.
    if not answer('Reboot requests started'):
        api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'],
            'text':'That reboot click expired. Send reboot for a fresh menu. No servers were rebooted.'})
        return
    recent = {r['server_id'] for r in db.execute('SELECT server_id FROM reboot_requests WHERE requested_at>?', (now-180,))}
    # Consume before any network action. Crashes/ambiguous SSH outcomes never auto-retry a reboot.
    changed = db.execute("UPDATE reboot_menus SET state='dispatched' WHERE nonce=? AND state='open'", (nonce,)).rowcount
    if not changed: return
    for server_id in selected:
        if server_id not in recent:
            db.execute('INSERT OR REPLACE INTO reboot_requests VALUES (?,?)', (server_id, now))
    db.commit()
    lines = []
    for server_id in sorted(selected):
        name = allowed[server_id]['name']
        if server_id in recent:
            lines.append(name + ': skipped, a reboot was recently requested.')
            continue
        try:
            vps.reboot(server_id)  # Reloads inventory and rechecks n8n protection independently.
            lines.append(name + ': reboot scheduled in 1 minute.')
        except Exception:
            lines.append(name + ': reboot could not be confirmed. Check the server before trying again.')
    api(settings['token'], 'sendMessage', {'chat_id':settings['chat_id'], 'text':'\n'.join(lines)})
    api(settings['token'], 'editMessageReplyMarkup', {'chat_id':settings['chat_id'], 'message_id':row['message_id'], 'reply_markup':{'inline_keyboard':[]}})


def tick(db, settings, api, lock_path, now=None):
    if not settings.get('token') or not settings.get('chat_id'):
        return
    init(db)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return  # Interactive bot setup owns getUpdates temporarily.
        offset = db.execute('SELECT offset FROM bot_cursor WHERE id=1').fetchone()[0]
        updates = api(settings['token'], 'getUpdates', {'offset':offset, 'timeout':1, 'limit':100,
            'allowed_updates':['message','callback_query']})
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
                # A rejected callback response must not discard the rest of this
                # batch or pause all buttons for the alert retry interval.
                if not isinstance(error, TelegramError):
                    raise
                db.rollback()
                print('Telegram update response failed (method=' + error.method +
                      ' code=' + str(error.code) + ' reason=' + error.reason + ').', flush=True)
                if error.code == 429:
                    raise
