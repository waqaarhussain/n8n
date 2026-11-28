"""Private, owner-only AI task capture and weekday 09:30 UK checklists."""
import json
import hashlib
import re
import subprocess
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram_errors import TelegramError

LONDON = ZoneInfo('Europe/London')
MAX_NOTES = 4000


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS task_inputs (
        id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL,
        text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        next_attempt REAL NOT NULL DEFAULT 0, fallback INTEGER NOT NULL DEFAULT 0,
        UNIQUE(chat_id,message_id));
      CREATE TABLE IF NOT EXISTS work_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
        input_id INTEGER NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL,
        source_key TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
      CREATE UNIQUE INDEX IF NOT EXISTS open_work_task_source
        ON work_tasks(chat_id,source_key) WHERE completed=0;
      CREATE TABLE IF NOT EXISTS work_digest_cache (
        fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS task_daily (
        chat_id TEXT NOT NULL, day TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0, PRIMARY KEY(chat_id,day));
    ''')


def key(text):
    return re.sub(r'\s+', ' ', text).strip().casefold()


def send(settings, api, text):
    # Plain text only: notes cannot inject formatting or bot actions.
    return api(settings['token'],'sendMessage',{'chat_id':settings['chat_id'],'text':text})


def checklist(db, chat_id, completed=False):
    return db.execute('SELECT * FROM work_tasks WHERE chat_id=? AND completed=? ORDER BY id',
                      (str(chat_id),int(completed))).fetchall()


def render(rows, heading='Work checklist', digest=None):
    if not rows:
        return heading+'\nNothing outstanding ✅'
    actions = {item['id']:item['text'] for item in digest['items']} if digest else {}
    lines = [str(r['id'])+'. ☐ '+actions.get(r['id'],r['title']) for r in rows]
    example = ','.join(str(r['id']) for r in rows[:2])
    first = str(rows[0]['id'])
    help_text = ('Reply done '+first+' to mark it complete.' if len(rows)==1 else
                 'Reply done '+first+' or done '+example+' using the item numbers.')
    overview = (digest['overview']+'\n\n') if digest else ''
    return heading+'\n\n'+overview+'\n'.join(lines)+'\n\n'+help_text+' Send tasks to view the list.'


def fingerprint(chat_id, rows):
    value = {'chat':str(chat_id),'tasks':[{'id':r['id'],'title':r['title'],'source':r['source']} for r in rows]}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def summarise_digest(rows):
    data = {'mode':'digest','tasks':[{'id':r['id'],'title':r['title'],'source':r['source']} for r in rows]}
    result = subprocess.run(['docker','compose','exec','-T','school-bridge','python','task_extract.py'],
                            input=json.dumps(data),text=True,check=True,stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,timeout=55)
    return json.loads(result.stdout)


def validate_digest(value, rows):
    expected = {r['id']:r for r in rows}
    if not isinstance(value,dict) or set(value)!={'overview','items'}: raise ValueError('Invalid summary')
    if not isinstance(value['overview'],str) or not 1<=len(value['overview'].strip())<=600: raise ValueError('Invalid overview')
    items = value['items']
    if not isinstance(items,list) or len(items)!=len(rows): raise ValueError('Missing summary items')
    ids = []
    for item in items:
        if not isinstance(item,dict) or set(item)!={'id','text','source'}: raise ValueError('Invalid item')
        identity = item['id']
        if type(identity) is not int or identity not in expected: raise ValueError('Unknown task')
        if not isinstance(item['text'],str) or not 1<=len(item['text'].strip())<=250: raise ValueError('Invalid action')
        if not isinstance(item['source'],str) or not item['source'].strip() or item['source'] not in expected[identity]['source']: raise ValueError('Invalid evidence')
        item['text'] = re.sub(r'\s+',' ',item['text']).strip()
        ids.append(identity)
    if len(set(ids))!=len(ids): raise ValueError('Duplicate task')
    return value


def send_checklist(db, settings, api, heading='Work checklist', ai_summary=True):
    init(db)
    rows = checklist(db,settings['chat_id'])
    snapshot = fingerprint(settings['chat_id'],rows)
    messages = []
    for offset in range(0,max(1,len(rows)),8):
        batch = rows[offset:offset+8]
        digest = None
        cache_key = fingerprint(settings['chat_id'],batch)
        cached = db.execute('SELECT payload FROM work_digest_cache WHERE fingerprint=?',(cache_key,)).fetchone()
        try:
            if cached:
                digest = validate_digest(json.loads(cached['payload']),batch)
            elif batch and ai_summary:
                digest = validate_digest(summarise_digest(batch),batch)
                db.execute('INSERT OR REPLACE INTO work_digest_cache VALUES (?,?)',(cache_key,json.dumps(digest)))
                db.commit()
        except Exception:
            digest = None
        text = render(batch,heading,digest)
        if batch and ai_summary and digest is None:
            text = text.replace(heading+'\n\n',heading+'\n\nAI summary unavailable. Here is your saved checklist.\n\n',1)
        messages.append(text)
    # A task can be completed while the AI request is in flight. Never send the
    # old summary or old checklist after the active set has changed.
    current = checklist(db,settings['chat_id'])
    if fingerprint(settings['chat_id'],current)!=snapshot:
        if not current: return
        messages = [render(current[i:i+8],heading) for i in range(0,max(1,len(current)),8)]
    for text in messages:
        send(settings,api,text)


def handle(db, settings, update, api, now=None):
    """Return true only when this update belongs to the task feature."""
    now = time.time() if now is None else now
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    user = message.get('from') or {}
    if (chat.get('type')!='private' or str(chat.get('id'))!=str(settings.get('chat_id'))
        or str(user.get('id'))!=str(settings.get('chat_id')) or user.get('is_bot')):
        return False
    if message.get('forward_origin') or message.get('forward_date') or message.get('via_bot'):
        return False
    # Owner notes may arrive after an outage; unlike reboots they must not be
    # discarded due to transport delay or host clock differences. Stable IDs
    # and the persisted input/update cursors make task commands idempotent.
    raw = message.get('text','').strip()
    text = raw.casefold()
    if not raw:
        return False
    # Leave reboot, numeric selection and cancel exclusively to the reboot router.
    if re.fullmatch(r'/?reboot(?:@[a-z0-9_]+)?',text) or re.fullmatch(r'[0-9]+(?:\s*,\s*[0-9]+)*',text) or text in {'cancel','/cancel'}:
        return False
    if text.startswith('/') and text not in {'/tasks','/todo','/done','/reopen','/help','/start'} and not text.startswith(('/done ','/reopen ')):
        return False
    init(db)
    if text in {'tasks','todo','/tasks','/todo'}:
        send_checklist(db,settings,api,ai_summary=False)
        return True
    if text in {'help','/help','/start'}:
        send(settings,api,'Send rough work notes to add tasks. AI tidies the wording.\n'
             'tasks: outstanding list\ndone followed by task numbers: complete those tasks\n'
             'reopen followed by a task number: restore that task\n'
             'Daily checklist: Monday–Friday, 09:30 UK time.\n'
             'pause / resume: stop or start weekday reminders.')
        return True
    match = re.fullmatch(r'/?(done|reopen)(?:\s+(.*))?',text)
    if match:
        ids_text = match.group(2) or ''
        if len(ids_text)>512 or not re.fullmatch(r'[0-9]+(?:\s*,\s*[0-9]+)*',ids_text):
            send(settings,api,'Use done or reopen followed by the task numbers shown in your checklist, separated by commas.')
            return True
        ids = sorted({int(x.strip()) for x in ids_text.split(',')})
        rows = db.execute('SELECT * FROM work_tasks WHERE chat_id=? AND id IN ('+','.join('?' for _ in ids)+')',
                          (str(settings['chat_id']),*ids)).fetchall()
        if len(rows)!=len(ids):
            send(settings,api,'One or more task numbers were not found. Send tasks to check the list. Nothing changed.')
            return True
        completing = match.group(1)=='done'
        try:
            db.executemany('UPDATE work_tasks SET completed=? WHERE id=? AND chat_id=?',
                           [(int(completing),i,str(settings['chat_id'])) for i in ids])
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            send(settings,api,'A matching task is already open. Nothing changed; send tasks to check the list.')
            return True
        send(settings,api,('Completed ✅ ' if completing else 'Reopened: ')+', '.join(map(str,ids)))
        send_checklist(db,settings,api,ai_summary=False)
        return True
    if len(raw)>MAX_NOTES or len([x for x in raw.splitlines() if x.strip()])>30:
        send(settings,api,'Please split your notes into messages of up to 4,000 characters and 30 lines.')
        return True
    added = db.execute('INSERT OR IGNORE INTO task_inputs(chat_id,message_id,text) VALUES (?,?,?)',
                       (str(settings['chat_id']),message['message_id'],raw)).rowcount
    db.commit()
    if added:
        send(settings,api,'Notes saved. I’m tidying them into your work checklist…')
    return True


def summarise(text):
    result = subprocess.run(['docker','compose','exec','-T','school-bridge','python','task_extract.py'],
                            input=json.dumps({'text':text}),text=True,check=True,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=55)
    return json.loads(result.stdout)


def capture_tick(db, settings, api, now=None, extract=None):
    now = time.time() if now is None else now
    init(db)
    if not settings.get('token') or not settings.get('chat_id'): return
    row = db.execute("SELECT * FROM task_inputs WHERE chat_id=? AND state IN ('pending','processing','ready') AND next_attempt<=? ORDER BY id LIMIT 1",
                     (str(settings['chat_id']),now)).fetchone()
    if not row: return
    if row['state']!='ready':
        db.execute("UPDATE task_inputs SET state='processing',next_attempt=? WHERE id=?",(now+90,row['id']))
        db.commit()
        fallback = False
        try:
            drafts = (extract or summarise)(row['text'])
            if not isinstance(drafts,list) or len(drafts)>30: raise ValueError()
            for draft in drafts:
                if (set(draft)!={'title','source'} or not isinstance(draft['title'],str)
                    or not 1<=len(draft['title'].strip())<=180 or not isinstance(draft['source'],str)
                    or not draft['source'].strip() or draft['source'] not in row['text']): raise ValueError()
        except Exception:
            # Raw notes stay safe and usable even while OpenAI or Docker is down.
            fallback = True
            drafts = [{'title':line[:177]+'…' if len(line)>180 else line,'source':line}
                      for line in row['text'].splitlines() if line.strip()]
        for draft in drafts:
            db.execute('INSERT OR IGNORE INTO work_tasks(chat_id,input_id,title,source,source_key) VALUES (?,?,?,?,?)',
                       (row['chat_id'],row['id'],re.sub(r'\s+',' ',draft['title']).strip(),draft['source'],key(draft['source'])))
        db.execute("UPDATE task_inputs SET state='ready',fallback=?,next_attempt=0 WHERE id=?",(int(fallback),row['id']))
        db.commit()
    current = db.execute('SELECT * FROM task_inputs WHERE id=?',(row['id'],)).fetchone()
    try:
        if current['fallback']:
            send(settings,api,'AI tidying is unavailable. I kept your notes as checklist items instead; your original notes are saved.')
        send_checklist(db,settings,api,'Updated work checklist')
    except TelegramError as error:
        db.execute('UPDATE task_inputs SET next_attempt=? WHERE id=?',(now+max(30,error.retry_after),row['id']))
        db.commit()
        return
    db.execute("UPDATE task_inputs SET state='done' WHERE id=?",(row['id'],))
    db.commit()


def reminder_tick(db, settings, api, now=None):
    now = time.time() if now is None else now
    init(db)
    if not settings.get('token') or not settings.get('chat_id') or not settings.get('work_reminders',True): return
    local = datetime.fromtimestamp(now,LONDON)
    if local.weekday()>4 or (local.hour,local.minute)<(9,30): return
    chat,day = str(settings['chat_id']),local.date().isoformat()
    db.execute('INSERT OR IGNORE INTO task_daily(chat_id,day) VALUES (?,?)',(chat,day))
    row = db.execute('SELECT * FROM task_daily WHERE chat_id=? AND day=?',(chat,day)).fetchone()
    if row['sent'] or row['next_attempt']>now:
        db.commit()
        return
    # Lease before network I/O. Interrupted sends resume later, never every poll.
    db.execute('UPDATE task_daily SET next_attempt=? WHERE chat_id=? AND day=?',(now+60,chat,day))
    db.commit()
    if checklist(db,chat):
        try:
            send_checklist(db,settings,api,'Your work briefing • '+local.strftime('%a %d %b'))
        except TelegramError as error:
            db.execute('UPDATE task_daily SET next_attempt=? WHERE chat_id=? AND day=?',(now+max(30,error.retry_after),chat,day))
            db.commit()
            return
    db.execute('UPDATE task_daily SET sent=1 WHERE chat_id=? AND day=?',(chat,day))
    db.commit()
