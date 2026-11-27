import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import telegram as tg
import work_tasks as tasks
import task_extract as extraction

SETTINGS={'token':'private-token','chat_id':'123','bot_username':'alertsbot'}
NOW=datetime.fromisoformat('2026-11-25T09:00:00+00:00').timestamp()
NOTES='ITSM - Alex Example Ticket\nSupplier update\nboard member stuff'


def message(text=NOTES, number=1, **changes):
    value={'message_id':number,'date':NOW,'chat':{'id':123,'type':'private'},'from':{'id':123},'text':text}
    value.update(changes)
    return {'update_id':number,'message':value}


@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(tg,'STATE',tmp_path/'tasks.sqlite3')
    db=tg.database();tasks.init(db)
    calls=[]
    def api(token,method,payload):
        assert token==SETTINGS['token'] and method=='sendMessage'
        calls.append(payload)
        return {'message_id':100+len(calls)}
    yield db,calls,api
    db.close()


def draft(text):
    return [{'title':line,'source':line} for line in text.splitlines()]


def capture(state, text=NOTES, number=1, extract=draft):
    db,calls,api=state
    assert tasks.handle(db,SETTINGS,message(text,number),api,NOW)
    tasks.capture_tick(db,SETTINGS,api,NOW,extract)
    return tasks.checklist(db,'123')


def test_raw_notes_saved_before_ai_and_receipt_is_immediate(state):
    db,calls,api=state
    assert tasks.handle(db,SETTINGS,message(),api,NOW)
    assert db.execute('SELECT text,state FROM task_inputs').fetchone()[:]==(NOTES,'pending')
    assert not tasks.checklist(db,'123')
    assert calls[-1]['text'].startswith('Notes saved.')
    assert tg.STATE.stat().st_mode & 0o777 == 0o600


def test_ai_tidy_additive_and_done_ids_stay_stable(state):
    db,calls,api=state
    rows=capture(state)
    ids=[r['id'] for r in rows]
    tasks.handle(db,SETTINGS,message('done '+str(ids[0]),2),api,NOW)
    capture(state,'New board follow-up',3)
    assert [r['id'] for r in tasks.checklist(db,'123')]==[ids[1],ids[2],ids[2]+1]
    tasks.handle(db,SETTINGS,message('reopen '+str(ids[0]),4),api,NOW)
    assert tasks.checklist(db,'123')[0]['id']==ids[0]
    assert 'reply_markup' not in calls[-1]
    assert 'parse_mode' not in calls[-1]


def test_multiple_completions_removed_from_next_checklist(state):
    db,calls,api=state
    rows=capture(state)
    tasks.handle(db,SETTINGS,message('done '+','.join(str(r['id']) for r in rows[:2]),2),api,NOW)
    assert [r['title'] for r in tasks.checklist(db,'123')]==['board member stuff']
    assert 'Supplier update' not in calls[-1]['text']
    assert len(tasks.checklist(db,'123',completed=True))==2


def test_duplicate_telegram_message_and_repeated_note_do_not_duplicate_tasks(state):
    db,calls,api=state
    capture(state)
    capture(state,number=1)
    capture(state,number=2)
    assert len(tasks.checklist(db,'123'))==3
    assert db.execute('SELECT count(*) FROM task_inputs').fetchone()[0]==2


@pytest.mark.parametrize('changes',[
    {'chat':{'id':999,'type':'private'}}, {'chat':{'id':123,'type':'group'}},
    {'from':{'id':999}}, {'from':{'id':123,'is_bot':True}},
    {'date':NOW-121},{'date':NOW+31},{'forward_origin':{'type':'user'}},{'via_bot':{'id':1}},
])
def test_only_fresh_direct_owner_messages_are_captured(state,changes):
    db,calls,api=state
    assert not tasks.handle(db,SETTINGS,message(**changes),api,NOW)
    assert db.execute('SELECT count(*) FROM task_inputs').fetchone()[0]==0
    assert not calls


@pytest.mark.parametrize('text',['reboot','/reboot','Reboot','1','1,2','cancel','/start n8n_nonce','/unknown'])
def test_task_router_never_consumes_reboot_or_setup_commands(state,text):
    db,calls,api=state
    assert not tasks.handle(db,SETTINGS,message(text),api,NOW)
    assert not calls


def test_invalid_done_id_does_not_partially_complete_or_cross_chats(state):
    db,calls,api=state
    capture(state)
    tasks.handle(db,SETTINGS,message('done 1,999',2),api,NOW)
    assert len(tasks.checklist(db,'123'))==3
    other={**SETTINGS,'chat_id':'999'}
    tasks.handle(db,other,message('done 1',3,chat={'id':999,'type':'private'},**{'from':{'id':999}}),api,NOW)
    assert len(tasks.checklist(db,'123'))==3


def test_ai_failure_keeps_notes_as_items_and_sends_no_secrets(state):
    db,calls,api=state
    fail=MagicMock(side_effect=RuntimeError('private-key'))
    rows=capture(state,extract=fail)
    assert [r['source'] for r in rows]==NOTES.splitlines()
    assert any('AI tidying is unavailable' in p['text'] for p in calls)
    assert 'private-key' not in json.dumps(calls)


def test_ungrounded_ai_evidence_falls_back_to_original_notes(state):
    rows=capture(state,extract=lambda _: [{'title':'Invented action','source':'not in the supplied notes'}])
    assert [r['title'] for r in rows]==NOTES.splitlines()


def test_model_cannot_mark_tasks_complete_or_set_schedule(state):
    rows=capture(state,extract=lambda _: [{'title':'Supplier update','source':'Supplier update','completed':True}])
    assert len(rows)==3 and all(not row['completed'] for row in rows)


def test_confirmation_retry_does_not_run_ai_or_create_tasks_twice(state):
    db,calls,api=state
    tasks.handle(db,SETTINGS,message(),api,NOW)
    extract=MagicMock(side_effect=draft)
    fail=MagicMock(side_effect=tg.TelegramError(429,60))
    tasks.capture_tick(db,SETTINGS,fail,NOW,extract)
    assert db.execute('SELECT state FROM task_inputs').fetchone()[0]=='ready'
    tasks.capture_tick(db,SETTINGS,api,NOW+61,extract)
    extract.assert_called_once()
    assert len(tasks.checklist(db,'123'))==3
    assert db.execute('SELECT state FROM task_inputs').fetchone()[0]=='done'


def stamp(iso): return datetime.fromisoformat(iso).timestamp()


@pytest.mark.parametrize('iso,expected',[
    ('2026-11-25T09:29:59+00:00',0),('2026-11-25T09:30:00+00:00',1),
    ('2026-11-28T09:30:00+00:00',0),('2026-11-29T09:30:00+00:00',0),
    ('2026-07-01T08:29:59+00:00',0),('2026-07-01T08:30:00+00:00',1),
])
def test_0930_weekdays_uk_schedule_including_dst(state,iso,expected):
    db,calls,api=state
    capture(state)
    calls.clear()
    tasks.reminder_tick(db,SETTINGS,api,stamp(iso))
    assert len(calls)==expected
    tasks.reminder_tick(db,SETTINGS,api,stamp(iso))
    assert len(calls)==expected


def test_completed_list_does_not_send_empty_daily_reminder(state):
    db,calls,api=state
    capture(state)
    db.execute('UPDATE work_tasks SET completed=1');db.commit();calls.clear()
    tasks.reminder_tick(db,SETTINGS,api,stamp('2026-11-25T09:30:00+00:00'))
    assert not calls


def test_daily_retry_uses_current_outstanding_list_and_survives_restart(state):
    db,calls,api=state
    capture(state)
    now=stamp('2026-11-25T09:30:00+00:00')
    fail=MagicMock(side_effect=tg.TelegramError(429,120))
    tasks.reminder_tick(db,SETTINGS,fail,now)
    db.execute('UPDATE work_tasks SET completed=1 WHERE id=1');db.commit();calls.clear()
    with tg.database() as reopened:
        tasks.reminder_tick(reopened,SETTINGS,api,now+121)
        tasks.reminder_tick(reopened,SETTINGS,api,now+122)
    assert len(calls)==1 and 'Alex' not in calls[0]['text'] and 'Supplier update' in calls[0]['text']


def test_pausing_reminders_keeps_notes_and_manual_list_working(state):
    db,calls,api=state
    capture(state)
    paused={**SETTINGS,'work_reminders':False};calls.clear()
    tasks.reminder_tick(db,paused,api,stamp('2026-11-25T09:30:00+00:00'))
    assert not calls
    tasks.handle(db,paused,message('tasks',2),api,NOW)
    assert 'Supplier update' in calls[-1]['text']


def test_ai_uses_existing_model_and_key_with_evidence_validation(monkeypatch):
    response=MagicMock()
    response.json.return_value={'choices':[{'message':{'content':json.dumps({'tasks':[{'title':'Supplier update','source':'Supplier update'}]})}}]}
    client=MagicMock();client.__enter__.return_value.post.return_value=response
    monkeypatch.setattr(extraction.httpx,'Client',MagicMock(return_value=client))
    assert extraction.extract_tasks({'ai':{'api_key':'private','model':'existing-model'}},'Supplier update')==[{'title':'Supplier update','source':'Supplier update'}]
    post=client.__enter__.return_value.post
    assert post.call_args.kwargs['json']['model']=='existing-model'
    response.json.return_value={'choices':[{'message':{'content':json.dumps({'tasks':[{'title':'Invented','source':'absent'}]})}}]}
    with pytest.raises(ValueError):extraction.extract_tasks({'ai':{'api_key':'private'}},'Supplier update')


def test_large_lists_are_split_and_pending_processing_recovers(state):
    db,calls,api=state
    text='\n'.join('Action '+str(i) for i in range(20))
    tasks.handle(db,SETTINGS,message(text),api,NOW)
    db.execute("UPDATE task_inputs SET state='processing', next_attempt=?",(NOW-1,));db.commit()
    calls.clear()
    tasks.capture_tick(db,SETTINGS,api,NOW,draft)
    assert len(tasks.checklist(db,'123'))==20
    assert len(calls)==2 and all(len(p['text'])<4096 for p in calls)
