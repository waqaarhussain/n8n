import json
import sqlite3
from unittest.mock import MagicMock

import pytest
import telegram as shared
import work_bot as bot
import work_tasks as tasks
import reboot_menu

NOW=1_800_000_000
MAIN_TOKEN='111:'+('a'*25)
WORK_TOKEN='222:'+('b'*25)
VALUE={'token':WORK_TOKEN,'chat_id':'123','bot_username':'workbot'}


@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(bot,'CONFIG',tmp_path/'config/work.json')
    monkeypatch.setattr(bot,'STATE',tmp_path/'data/work.db')
    monkeypatch.setattr(bot,'MAIN_CONFIG',tmp_path/'config/main.json')
    monkeypatch.setattr(bot,'MAIN_STATE',tmp_path/'data/main.db')
    monkeypatch.setattr(shared,'CONFIG',bot.MAIN_CONFIG)
    monkeypatch.setattr(shared,'STATE',bot.MAIN_STATE)
    bot.MAIN_CONFIG.parent.mkdir()
    main={'token':MAIN_TOKEN,'chat_id':'123','bot_username':'opsbot','work_reminders':False}
    bot.MAIN_CONFIG.write_text(json.dumps(main))
    original=bot.MAIN_CONFIG.read_bytes()
    bot.configure()
    db=shared.database();bot.init(db)
    yield db,original
    db.close()


def update(text,number=1,owner=123):
    return {'update_id':number,'message':{'message_id':number,'date':NOW,
        'text':text,'chat':{'id':owner,'type':'private'},'from':{'id':owner}}}


def test_separate_bot_identity_blocks_reused_token_including_rotation(state):
    for token in (MAIN_TOKEN,'111:'+('z'*25)):
        with pytest.raises(ValueError):bot.separate({**VALUE,'token':token})
    bot.separate(VALUE)


def test_setup_rejects_original_bot_before_poll_or_api_calls(state,monkeypatch):
    monkeypatch.setattr(bot.getpass,'getpass',lambda _:MAIN_TOKEN)
    api=MagicMock();monkeypatch.setattr(shared,'api',api)
    with pytest.raises(SystemExit,match='existing VPS bot'):bot.setup()
    api.assert_not_called()
    assert not bot.CONFIG.exists()


def test_new_bot_setup_binds_existing_owner_and_keeps_original_config(state,monkeypatch):
    db,original=state
    monkeypatch.setattr(bot.getpass,'getpass',lambda _:WORK_TOKEN)
    monkeypatch.setattr(bot.secrets,'token_hex',lambda _:'nonce')
    monkeypatch.setattr('builtins.input',lambda _: '')
    def api(token,method,payload=None):
        assert token==WORK_TOKEN
        if method=='getMe':return {'username':'workbot'}
        if method=='getWebhookInfo':return {}
        if method=='getUpdates':return [update('/start work_nonce',1,999),update('/start work_nonce',2)]
        return {'message_id':3}
    monkeypatch.setattr(shared,'api',api)
    bot.setup()
    saved=json.loads(bot.CONFIG.read_text())
    assert saved['chat_id']=='123' and saved['token']==WORK_TOKEN
    assert saved['work_reminders'] is False
    assert bot.CONFIG.stat().st_mode & 0o777==0o600
    assert bot.MAIN_CONFIG.read_bytes()==original
    assert db.execute('SELECT offset FROM work_bot_control').fetchone()[0]==3


def legacy_data():
    db=sqlite3.connect(bot.MAIN_STATE)
    db.row_factory=sqlite3.Row
    tasks.init(db)
    db.execute("INSERT INTO task_inputs(id,chat_id,message_id,text,state) VALUES (1,'123',1,'Old notes','done')")
    db.executemany('INSERT INTO work_tasks VALUES (?,?,?,?,?,?,?)',[
        (1,'123',1,'Open action','Old notes','old notes',0),
        (2,'123',1,'Completed action','Completed','completed',1),
        (3,'999',1,'Other owner','Private','private',0)])
    db.execute("INSERT INTO task_daily VALUES ('123','2026-11-27',1,0)")
    db.commit();db.close()


def test_legacy_copy_preserves_ids_completion_daily_state_and_is_one_shot(state):
    db,original=state
    legacy_data()
    before=bot.MAIN_STATE.read_bytes()
    bot.migrate(db,'123');bot.migrate(db,'123')
    rows=db.execute('SELECT id,title,completed FROM work_tasks ORDER BY id').fetchall()
    assert [tuple(r) for r in rows]==[(1,'Open action',0),(2,'Completed action',1)]
    assert db.execute('SELECT sent FROM task_daily').fetchone()[0]==1
    assert db.execute('SELECT message_id FROM task_inputs').fetchone()[0]==-1
    assert bot.MAIN_STATE.read_bytes()==before
    assert bot.MAIN_CONFIG.read_bytes()==original
    api=MagicMock(return_value={'message_id':10})
    tasks.handle(db,VALUE,update('New note',1),api,NOW)
    assert db.execute('SELECT count(*) FROM task_inputs').fetchone()[0]==2


def test_migration_never_replaces_existing_destination_tasks(state):
    db,_=state
    legacy_data()
    db.execute("INSERT INTO task_inputs(chat_id,message_id,text) VALUES ('123',44,'New notes')");db.commit()
    with pytest.raises(ValueError,match='manual merge'):bot.migrate(db,'123')
    assert db.execute('SELECT text FROM task_inputs').fetchone()[0]=='New notes'
    assert db.execute('SELECT count(*) FROM work_tasks').fetchone()[0]==0


def test_work_bot_reboot_and_bare_numbers_never_reach_vps_router(state,monkeypatch):
    db,_=state
    controls=MagicMock();monkeypatch.setattr(reboot_menu,'process',controls)
    sent=[]
    def api(token,method,payload):
        assert token==WORK_TOKEN
        if method=='getUpdates':return [update('reboot'),update('1,2',2)]
        sent.append(payload)
        return {'message_id':10}
    bot.poll(db,VALUE,api,NOW)
    controls.assert_not_called()
    assert len(sent)==2 and all('other bot' in p['text'] for p in sent)
    assert db.execute('SELECT count(*) FROM task_inputs').fetchone()[0]==0


def test_original_bot_does_not_capture_work_notes(state,monkeypatch,tmp_path):
    db,_=state
    capture=MagicMock();monkeypatch.setattr(tasks,'handle',capture)
    controls=MagicMock();monkeypatch.setattr(reboot_menu,'process',controls)
    def api(token,method,payload):return [update('Meeting follow-up')]
    reboot_menu.tick(db,{'token':MAIN_TOKEN,'chat_id':'123'},api,tmp_path/'main-lock',NOW)
    capture.assert_not_called()
    controls.assert_called_once()


def test_work_poll_captures_notes_and_keeps_main_database_untouched(state):
    db,_=state
    legacy_data();before=bot.MAIN_STATE.read_bytes()
    calls=[]
    def api(token,method,payload):
        if method=='getUpdates':return [update('Supplier follow-up')]
        calls.append(payload);return {'message_id':9}
    bot.poll(db,VALUE,api,NOW)
    assert db.execute('SELECT text FROM task_inputs').fetchone()[0]=='Supplier follow-up'
    assert calls[-1]['text'].startswith('Notes saved')
    assert bot.MAIN_STATE.read_bytes()==before


def test_pause_only_updates_work_config_and_other_owners_are_ignored(state):
    db,original=state
    sent=[]
    def api(token,method,payload):
        if method=='getUpdates':return [update('pause'),update('resume',2,999)]
        sent.append(payload);return {'message_id':4}
    bot.poll(db,dict(VALUE),api,NOW)
    assert json.loads(bot.CONFIG.read_text())['work_reminders'] is False
    assert bot.MAIN_CONFIG.read_bytes()==original
    assert len(sent)==1
