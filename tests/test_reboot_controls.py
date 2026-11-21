import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import vps
import reboot_menu as menu
import telegram as tg

SETTINGS = {'token':'private-token', 'chat_id':'123', 'bot_username':'alertsbot'}
NOW = 1_800_000_000


def server(number, **kw):
    return dict(id=f'{number:012x}', name=f'Server {number}', host=f'192.0.2.{number}', port=22,
                credential_id=f'Cred{number}', credential_name=f'Cred {number}',
                is_n8n_host=kw.get('is_n8n_host', False), reboot_ready=True)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, 'STATE', tmp_path/'bot.sqlite3')
    monkeypatch.setattr(vps, 'CONFIG', tmp_path/'vps.json')
    monkeypatch.setattr(vps, 'KEYS', tmp_path/'keys')
    monkeypatch.setattr(vps, 'self_addresses', lambda: {'192.0.2.3','127.0.0.1'})
    inventory = {'servers':[server(1),server(2),server(3,is_n8n_host=True)], 'telegram_reboot_enabled':True}
    vps.save(inventory)
    db = tg.database(); menu.init(db)
    calls = []
    def api(token, method, payload):
        assert token == SETTINGS['token']
        calls.append((method,payload))
        return {'message_id':77} if method=='sendMessage' else True
    yield db, calls, api, inventory
    db.close()


def message(identity=1, **kw):
    return {'update_id':identity, 'message':{'message_id':1, 'date':NOW, 'chat':{'id':123,'type':'private'},
        'from':{'id':123}, 'text':'reboot', **kw}}


def open_menu(state):
    db, calls, api, _ = state
    menu.process(db, SETTINGS, message(), api, NOW)
    return db.execute("SELECT nonce FROM reboot_menus WHERE state='open'").fetchone()[0]


def callback(nonce, action):
    return {'callback_query':{'id':'callback1','from':{'id':123},'message':{'message_id':77,'chat':{'id':123,'type':'private'}},
        'data':'rb:'+nonce+':'+action}}


def test_menu_selects_multiple_and_never_offers_host(state, monkeypatch):
    db, calls, api, _ = state
    nonce = open_menu(state)
    text = json.dumps(calls[-1][1])
    assert 'Server 1' in text and 'Server 2' in text and 'Server 3' not in text
    for number in (1,2):
        menu.process(db, SETTINGS, callback(nonce, f'pick:{number:012x}'), api, NOW)
    assert len(json.loads(db.execute('SELECT selected FROM reboot_menus').fetchone()[0])) == 2
    reboot = MagicMock()
    monkeypatch.setattr(vps, 'reboot', reboot)
    def check(sid):
        assert db.execute('SELECT state FROM reboot_menus').fetchone()[0] == 'dispatched'
    reboot.side_effect = check
    menu.process(db, SETTINGS, callback(nonce, 'go'), api, NOW)
    assert [c.args[0] for c in reboot.call_args_list] == [server(1)['id'],server(2)['id']]
    menu.process(db, SETTINGS, callback(nonce, 'go'), api, NOW+1)
    assert reboot.call_count == 2


@pytest.mark.parametrize('change', [
    {'chat':{'id':456,'type':'private'}}, {'chat':{'id':123,'type':'group'}}, {'from':{'id':456}},
    {'from':{'id':123,'is_bot':True}}, {'date':NOW-121}, {'date':NOW+31},
    {'forward_origin':{'type':'user'}}, {'text':'reboot 192.0.2.3'}, {'text':'/reboot@otherbot'},
])
def test_untrusted_stale_or_arbitrary_commands_do_nothing(state, change):
    db, calls, api, _ = state
    menu.process(db, SETTINGS, message(**change), api, NOW)
    assert db.execute('SELECT count(*) FROM reboot_menus').fetchone()[0] == 0 and not calls


def test_deselect_cancel_and_expired_buttons_never_reboot(state, monkeypatch):
    db, calls, api, _ = state
    nonce = open_menu(state)
    reboot = MagicMock(); monkeypatch.setattr(vps, 'reboot', reboot)
    pick = callback(nonce, 'pick:'+server(1)['id'])
    menu.process(db, SETTINGS, pick, api, NOW); menu.process(db, SETTINGS, pick, api, NOW)
    assert json.loads(db.execute('SELECT selected FROM reboot_menus').fetchone()[0]) == []
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW)
    menu.process(db, SETTINGS, pick, api, NOW+301)
    menu.process(db, SETTINGS, callback(nonce,'cancel'), api, NOW)
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW)
    reboot.assert_not_called()


def test_forged_host_selection_and_new_protection_are_rechecked(state, monkeypatch):
    db, calls, api, inventory = state
    nonce = open_menu(state)
    db.execute('UPDATE reboot_menus SET selected=?', (json.dumps([server(3)['id']]),));db.commit()
    reboot = MagicMock(); monkeypatch.setattr(vps,'reboot',reboot)
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW)
    reboot.assert_not_called()
    menu.process(db, SETTINGS, callback(nonce,'pick:'+server(1)['id']), api, NOW)
    inventory['protected_hosts'] = [server(1)['host']];vps.save(inventory)
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW)
    reboot.assert_not_called()


def test_partial_ssh_failure_continues_other_selection_without_retry(state, monkeypatch):
    db, calls, api, _ = state
    nonce = open_menu(state)
    for number in (1,2): menu.process(db, SETTINGS, callback(nonce,f'pick:{number:012x}'), api, NOW)
    reboot = MagicMock(side_effect=[subprocess.TimeoutExpired('ssh',25),None]);monkeypatch.setattr(vps,'reboot',reboot)
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW)
    assert reboot.call_count == 2
    text = next(payload['text'] for method,payload in reversed(calls) if method=='sendMessage')
    assert 'Server 1: reboot could not be confirmed' in text and 'Server 2: reboot scheduled' in text
    menu.process(db, SETTINGS, callback(nonce,'go'), api, NOW+1)
    assert reboot.call_count == 2


def test_cursor_is_persistent_and_disabled_controls_do_not_poll(state, tmp_path):
    db, calls, base_api, inventory = state
    def api(token,method,payload):
        if method=='getUpdates': return [message(identity=10)]
        return base_api(token,method,payload)
    for _ in range(2): menu.tick(db, SETTINGS, api, tmp_path/'lock', NOW)
    assert len([c for c in calls if c[0]=='sendMessage']) == 1
    assert db.execute('SELECT offset FROM bot_cursor').fetchone()[0] == 11
    inventory['telegram_reboot_enabled']=False;vps.save(inventory)
    never=MagicMock();menu.tick(db,SETTINGS,never,tmp_path/'lock',NOW)
    never.assert_not_called()


def test_protected_host_is_blocked_below_telegram_layer(state, monkeypatch):
    runner=MagicMock();monkeypatch.setattr(vps,'run',runner)
    with pytest.raises(ValueError):vps.reboot(server(3)['id'])
    runner.assert_not_called()
    with pytest.raises(ValueError):vps.setup_key(server(3),{'host':'192.0.2.3'})
    runner.assert_not_called()


def test_ssh_uses_pinned_keys_no_password_and_no_shell(state, monkeypatch):
    vps.KEYS.mkdir()
    sid=server(1)['id']
    (vps.KEYS/sid).write_text('private key')
    (vps.KEYS/(sid+'.known_hosts')).write_text('pinned key')
    runner=MagicMock();monkeypatch.setattr(vps,'run',runner)
    vps.reboot(sid)
    args=runner.call_args.args
    assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
    assert args[-2:] == ('root@192.0.2.1','reboot')
    assert 'password' not in str(args).lower()


def test_multi_server_workflow_preserves_schedule_attempts_all_and_host_last(state):
    wf=vps.build_workflow([server(3,is_n8n_host=True),server(1),server(2)])
    ssh=[n for n in wf['nodes'] if n['type']=='n8n-nodes-base.ssh']
    assert [n['credentials']['sshPassword']['id'] for n in ssh]==['Cred1','Cred2','Cred3']
    assert all(n['onError']=='continueRegularOutput' for n in ssh)
    assert all('/run/n8n-reboot-pending' in n['parameters']['command'] for n in ssh)
    assert wf['active'] is False and wf['id']==vps.WORKFLOW_ID
    assert wf['nodes'][0]['parameters']['rule']['interval'][0]['expression']=='0 0 * * 1'
    assert any(n['name']=='Any update failed?' for n in wf['nodes'])
    assert any(n['name']=='VPS reboot summary' for n in wf['nodes'])
    assert not any('shutdown -r' in n.get('parameters',{}).get('command','') for n in wf['nodes'])


def test_reboot_key_only_command_is_narrow():
    assert '/sbin/shutdown -r +1' in vps.FORCED_COMMAND
    assert '/var/lock/n8n-weekly-apt.lock' in vps.FORCED_COMMAND
    assert 'n8n-reboot-pending' in vps.FORCED_COMMAND


def test_credential_export_is_scoped_and_always_erases_temporary_plaintext(monkeypatch):
    calls=[]
    def docker(*args, **kwargs):
        calls.append((args,kwargs))
        if args[-2:-1] == ('cat',):
            return MagicMock(stdout=json.dumps([{'type':'sshPassword','data':{'host':'192.0.2.1','password':'private'}}]))
        return MagicMock(stdout='')
    monkeypatch.setattr(vps,'docker',docker)
    assert vps.credential('Cred1')['password']=='private'
    assert '--id=Cred1' in calls[0][0][-1] and '--decrypted' in calls[0][0][-1]
    assert calls[-1][0][-3:-1] == ('rm','-f')
    assert 'private' not in str([args for args,kwargs in calls])


def test_existing_vps_is_migrated_without_changing_credentials(state, monkeypatch):
    _,_,_,settings=state
    settings['servers']=[]
    native_nodes=[{'type':'n8n-nodes-base.ssh','credentials':{'sshPassword':{'id':'Original','name':'Original SSH'}}},
        {'name':'VPS display name','type':'n8n-nodes-base.code','parameters':{'jsCode':'return [{json: {vps_name: "Original friendly name"}}];'}}]
    monkeypatch.setattr(vps,'docker',lambda *args,**kw:MagicMock(stdout=json.dumps({'nodes':native_nodes})))
    monkeypatch.setattr(vps,'credential',lambda _: {'host':'192.0.2.1','port':22,'username':'root','password':'never-store'})
    vps.migrate(settings)
    assert settings['servers'][0]['name']=='Original friendly name'
    assert settings['servers'][0]['credential_id']=='Original'
    assert 'never-store' not in vps.CONFIG.read_text()


def test_reboot_cooldown_blocks_fresh_menu_repetition(state, monkeypatch):
    db, calls, api, _=state
    reboot=MagicMock();monkeypatch.setattr(vps,'reboot',reboot)
    for stamp in (NOW,NOW+10):
        nonce=open_menu(state)
        menu.process(db,SETTINGS,callback(nonce,'pick:'+server(1)['id']),api,stamp)
        menu.process(db,SETTINGS,callback(nonce,'go'),api,stamp)
    assert reboot.call_count==1


def test_failed_batch_can_report_reboots_from_successful_progress_nodes():
    from test_vps_maintenance import flatten
    result={'maintenance_version':1,'vps_name':'External VPS','update_status':'success','reboot_required':True}
    failed={**result,'vps_name':'Failed VPS','update_status':'failed','reboot_required':False}
    graph={'resultData':{'runData':{'Result 000000000001':[{'data':{'main':[[{'json':result}]]}}],
        'Result 000000000002':[{'data':{'main':[[{'json':failed}]]}}]}}}
    assert tg.reboot_names(flatten(graph))==['External VPS']


def test_menu_polling_is_responsive_without_speeding_failure_queries(state, tmp_path, monkeypatch):
    db,_,_,inventory=state
    (tmp_path/'data').mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tg,'config',lambda:SETTINGS)
    db.execute("INSERT INTO subscriptions VALUES ('school',1,'2026-11-21')");db.commit()
    alerts=MagicMock();controls=MagicMock()
    monkeypatch.setattr(tg,'tick',alerts)
    monkeypatch.setattr(tg,'verify_schema',MagicMock())
    monkeypatch.setattr(menu,'tick',controls)
    clock=[0]
    monkeypatch.setattr(tg.time,'time',lambda:clock[0])
    pauses=[]
    class Done(BaseException):pass
    def sleep(delay):
        pauses.append(delay);clock[0]+=2
        if len(pauses)==12:raise Done()
    monkeypatch.setattr(tg.time,'sleep',sleep)
    with pytest.raises(Done):tg.monitor()
    assert controls.call_count==12
    assert alerts.call_count==2
    assert pauses==[1]*12


def test_key_setup_rejects_changed_credential_target_before_any_ssh(state, monkeypatch):
    runner=MagicMock();monkeypatch.setattr(vps,'run',runner)
    with pytest.raises(ValueError,match='target differs'):
        vps.setup_key(server(1),{'host':'192.0.2.3','port':22,'username':'root'})
    runner.assert_not_called()
    mapped=server(1);mapped['host']='::ffff:192.0.2.3'
    assert vps.protected(mapped)
