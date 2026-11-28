"""Separate owner-only work checklist bot. No VPS command router is imported."""
from contextlib import closing
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time

import telegram as shared
from telegram_errors import TelegramError
import work_tasks

CONFIG = Path('config/work-telegram.json')
STATE = Path('data/work-telegram.sqlite3')
MAIN_CONFIG = Path('config/telegram.json')
MAIN_STATE = Path('data/telegram.sqlite3')


def configure():
    # Shared utilities run in this dedicated process with dedicated files.
    shared.CONFIG, shared.STATE = CONFIG, STATE


def main_config():
    return json.loads(MAIN_CONFIG.read_text()) if MAIN_CONFIG.exists() else {}


def separate(settings, main=None):
    main = main_config() if main is None else main
    token, other = settings.get('token',''), main.get('token','')
    if token and other and token.split(':',1)[0] == other.split(':',1)[0]:
        raise ValueError('Use a different bot for work checklists')


def settings():
    value = shared.config()
    separate(value)
    return value


def init(db):
    work_tasks.init(db)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS work_bot_control (
        id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL DEFAULT 0,
        last_poll REAL NOT NULL DEFAULT 0);
      INSERT OR IGNORE INTO work_bot_control(id) VALUES (1);
      CREATE TABLE IF NOT EXISTS work_bot_health (
        id INTEGER PRIMARY KEY CHECK(id=1), seen INTEGER NOT NULL DEFAULT 0,
        rejected INTEGER NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT '');
      INSERT OR IGNORE INTO work_bot_health(id) VALUES (1);
      CREATE TABLE IF NOT EXISTS work_bot_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')


def migrate(db, chat_id):
    """Copy only this owner's task tables once; leave the original bot untouched."""
    init(db)
    if db.execute("SELECT 1 FROM work_bot_meta WHERE key='legacy_tasks_v1'").fetchone(): return
    owner = str(chat_id)
    if MAIN_STATE.exists():
        with closing(sqlite3.connect(MAIN_STATE.resolve().as_uri()+'?mode=ro',uri=True)) as old:
            old.row_factory = sqlite3.Row
            names = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tables = [('task_inputs',['id','chat_id','message_id','text','state','next_attempt','fallback']),
                      ('work_tasks',['id','chat_id','input_id','title','source','source_key','completed']),
                      ('task_daily',['chat_id','day','sent','next_attempt'])]
            rows = {table:list(old.execute('SELECT '+','.join(cols)+' FROM '+table+' WHERE chat_id=?',(owner,)))
                    for table,cols in tables if table in names}
        if any(rows.values()) and any(db.execute('SELECT 1 FROM '+t+' LIMIT 1').fetchone() for t,_ in tables):
            raise ValueError('Existing work data requires a manual merge; nothing replaced')
        try:
            for table,cols in tables:
                for row in rows.get(table,[]):
                    values = [row[c] for c in cols]
                    if table=='task_inputs':
                        # The new bot has a separate Telegram message sequence.
                        values[cols.index('message_id')] = -abs(row['message_id'])
                    db.execute('INSERT INTO '+table+'('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',values)
            db.execute("INSERT INTO work_bot_meta VALUES ('legacy_tasks_v1',?)",(owner,))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return
    db.execute("INSERT INTO work_bot_meta VALUES ('legacy_tasks_v1',?)",(owner,));db.commit()


def setup():
    configure()
    old = shared.config()
    main = main_config()
    token = getpass.getpass('NEW work bot token [Enter keeps saved token]: ').strip() or old.get('token','')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}',token):
        raise SystemExit('Invalid token. Nothing changed.')
    candidate = {'token':token}
    try: separate(candidate,main)
    except ValueError: raise SystemExit('That is the existing VPS bot. Create a different bot for work notes.') from None
    bot = shared.api(token,'getMe')
    username = bot.get('username','')
    if not re.fullmatch(r'[A-Za-z0-9_]+',username): raise SystemExit('Invalid bot identity.')
    if shared.api(token,'getWebhookInfo').get('url'):
        raise SystemExit('This bot has a webhook. Use a new dedicated bot.')
    nonce = 'work_'+secrets.token_hex(12)
    expected_owner = str(main.get('chat_id') or old.get('chat_id') or '')
    print('Open https://t.me/'+username+'?start='+nonce+' and press START in your private chat.')
    offset = None
    while True:
        if input('After pressing START, press Enter here (q cancels): ').strip().lower()=='q':
            raise SystemExit('Cancelled. Existing bots unchanged.')
        for _ in range(5):
            payload = {'timeout':0,'limit':100,'allowed_updates':['message']}
            if offset is not None: payload['offset']=offset
            updates = shared.api(token,'getUpdates',payload)
            for update in updates:
                offset = update['update_id']+1
                msg = update.get('message',{})
                chat, user = msg.get('chat',{}),msg.get('from',{})
                if (chat.get('type')!='private' or msg.get('text')!='/start '+nonce
                    or str(user.get('id'))!=str(chat.get('id')) or user.get('is_bot')
                    or msg.get('forward_origin') or msg.get('forward_date')): continue
                if expected_owner and str(chat.get('id'))!=expected_owner: continue
                value = {'token':token,'chat_id':str(chat['id']),'bot_username':username,
                         'work_reminders':old.get('work_reminders',main.get('work_reminders',True))}
                with closing(shared.database()) as db:
                    migrate(db,value['chat_id'])
                    db.execute('UPDATE work_bot_control SET offset=? WHERE id=1',(offset,));db.commit()
                shared.save_config(value)
                shared.api(token,'sendMessage',{'chat_id':value['chat_id'],
                    'text':'Work bot connected ✅\nSend rough notes or tasks. Use done or reopen followed by task numbers.\nReminders: Monday–Friday 09:30 UK time.'})
                print('Work bot connected. Existing tasks preserved. The VPS bot is unchanged.')
                return
            if len(updates)<100: break
        print('Matching START message not found yet. Use the exact link in your private owner chat.')


def rejection_reason(update, value):
    msg = update.get('message') or {}
    if not msg: return 'NO_TEXT_MESSAGE'
    if msg.get('chat',{}).get('type')!='private': return 'NOT_PRIVATE'
    if str(msg.get('chat',{}).get('id'))!=str(value.get('chat_id')): return 'CHAT_MISMATCH'
    if str(msg.get('from',{}).get('id'))!=str(value.get('chat_id')): return 'SENDER_MISMATCH'
    if msg.get('from',{}).get('is_bot'): return 'BOT_SENDER'
    if any(msg.get(k) for k in ('forward_origin','forward_date','via_bot')): return 'FORWARDED'
    return ''


def direct_owner(update, value, now=None):
    return not rejection_reason(update,value)


def poll(db, value, api, now=None):
    separate(value)
    init(db)
    offset = db.execute('SELECT offset FROM work_bot_control WHERE id=1').fetchone()[0]
    updates = api(value['token'],'getUpdates',{'offset':offset,'timeout':20,'limit':100,'allowed_updates':['message']})
    # Record response time, not the time before a long-poll request started.
    observed = time.time() if now is None else now
    db.execute('UPDATE work_bot_control SET last_poll=? WHERE id=1',(observed,));db.commit()
    for update in updates:
        if update['update_id']<offset: continue
        reason = rejection_reason(update,value)
        if not reason:
            text = update['message'].get('text','').strip().casefold()
            if text in {'pause','resume','/pause','/resume'}:
                value['work_reminders'] = text.lstrip('/')=='resume'
                shared.save_config(value)
                work_tasks.send(value,api,'Weekday reminders '+('ON' if value['work_reminders'] else 'OFF')+'. Notes and task commands still work.')
            elif not work_tasks.handle(db,value,update,api,observed) and text:
                work_tasks.send(value,api,'This bot is for work notes. Send tasks, or use done/reopen followed by task numbers. VPS controls are in your other bot.')
            reason = 'OWNER_MESSAGE'
        # Persist the cursor after processing. A crash must not silently discard
        # an owner note. Capture deduplication and task commands tolerate replay.
        offset = update['update_id']+1
        rejected = int(reason!='OWNER_MESSAGE')
        db.execute('UPDATE work_bot_control SET offset=? WHERE id=1',(offset,))
        db.execute('UPDATE work_bot_health SET seen=seen+1,rejected=rejected+?,last_reason=? WHERE id=1',(rejected,reason))
        db.commit()
        print('Work bot update processed ('+reason+').',flush=True)


def worker(kind):
    while True:
        try:
            value = settings()
            if value.get('token') and value.get('chat_id'):
                with closing(shared.database()) as db:
                    tick = work_tasks.capture_tick if kind=='capture' else work_tasks.reminder_tick
                    tick(db,value,shared.api)
        except Exception as error:
            print('Work bot '+kind+' unavailable ('+type(error).__name__+'); retrying.',flush=True)
        time.sleep(2)


def monitor():
    configure()
    value = settings()
    if not value.get('token') or not value.get('chat_id'): raise SystemExit('Run sudo n8n-live work-bot first.')
    STATE.parent.mkdir(parents=True,exist_ok=True)
    lock = (STATE.parent/'work-bot-monitor.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with closing(shared.database()) as db: migrate(db,value['chat_id'])
    for kind in ('capture','reminders'):
        threading.Thread(target=worker,args=(kind,),name='work-'+kind,daemon=True).start()
    while True:
        delay = 0
        try:
            with closing(shared.database()) as db: poll(db,settings(),shared.api)
        except Exception as error:
            code = str(error.code) if isinstance(error,TelegramError) else type(error).__name__
            print('Work bot polling unavailable (code '+code+'); retrying.',flush=True)
            delay = max(2,error.retry_after) if isinstance(error,TelegramError) and error.code==429 else 2
        time.sleep(delay)


def status():
    configure()
    value = settings()
    print('Work bot: @'+value.get('bot_username','not configured'))
    print('Work reminders: '+('ON' if value.get('work_reminders',True) else 'OFF')+' | Monday–Friday 09:30 UK')
    print('Configured: '+('YES' if value.get('token') and value.get('chat_id') else 'NO'))
    if STATE.exists():
        with closing(shared.database()) as db:
            init(db)
            print('Outstanding tasks: '+str(len(work_tasks.checklist(db,value.get('chat_id','')))))
            poll = db.execute('SELECT last_poll FROM work_bot_control WHERE id=1').fetchone()[0]
            health = db.execute('SELECT * FROM work_bot_health WHERE id=1').fetchone()
            print('Last poll: '+(str(round(max(0,time.time()-poll)))+' seconds ago' if poll else 'not recorded'))
            print('Updates seen: '+str(health['seen'])+' | Rejected: '+str(health['rejected'])+' | Last: '+(health['last_reason'] or 'none'))
            print('Note batches: '+str([tuple(r) for r in db.execute('SELECT state,count(*) FROM task_inputs GROUP BY state')]))


def main():
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv)>1 else 'setup'
    try:
        {'setup':setup,'monitor':monitor,'status':status}[mode]()
    except KeyboardInterrupt:
        raise SystemExit('Cancelled.') from None
    except Exception as error:
        raise SystemExit('Work bot action failed ('+type(error).__name__+'). No secrets printed.') from None


if __name__=='__main__': main()
