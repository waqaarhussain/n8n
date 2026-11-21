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
    menu.process(db,SETTINGS,message(identity=12),api,NOW)
    assert 'controls are OFF' in calls[-1][1]['text']


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


def test_command_worker_never_queries_postgres_or_sends_alerts(state, tmp_path, monkeypatch):
    monkeypatch.setattr(tg, 'config', lambda: SETTINGS)
    sql = MagicMock(side_effect=AssertionError('Command worker must not use PostgreSQL'))
    alerts = MagicMock(side_effect=AssertionError('Command worker must not send failure alerts'))
    monkeypatch.setattr(tg, 'sql', sql)
    monkeypatch.setattr(tg, 'tick', alerts)
    controls = MagicMock()
    monkeypatch.setattr(menu, 'tick', controls)
    pauses = []
    class Done(BaseException): pass
    def sleep(delay):
        pauses.append(delay)
        if len(pauses) == 3: raise Done()
    monkeypatch.setattr(tg.time, 'sleep', sleep)
    with pytest.raises(Done): tg.command_monitor()
    assert controls.call_count == 3 and pauses == [0.2]*3
    sql.assert_not_called(); alerts.assert_not_called()


def test_alert_loop_starts_independent_daemon_even_when_database_checks_fail(state, tmp_path, monkeypatch):
    (tmp_path/'data').mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tg,'config',lambda:SETTINGS)
    state[0].execute("INSERT INTO subscriptions VALUES ('school',1,'2026-11-21')");state[0].commit()
    worker=MagicMock()
    monkeypatch.setattr(tg.threading,'Thread',worker)
    monkeypatch.setattr(tg,'verify_schema',MagicMock(side_effect=RuntimeError('Database unavailable')))
    class Done(BaseException):pass
    monkeypatch.setattr(tg.time,'sleep',MagicMock(side_effect=Done()))
    with pytest.raises(Done):tg.monitor()
    assert worker.call_args.kwargs['target'] is tg.command_monitor
    assert worker.call_args.kwargs['daemon'] is True
    worker.return_value.start.assert_called_once()


def test_key_setup_rejects_changed_credential_target_before_any_ssh(state, monkeypatch):
    runner=MagicMock();monkeypatch.setattr(vps,'run',runner)
    with pytest.raises(ValueError,match='target differs'):
        vps.setup_key(server(1),{'host':'192.0.2.3','port':22,'username':'root'})
    runner.assert_not_called()
    mapped=server(1);mapped['host']='::ffff:192.0.2.3'
    assert vps.protected(mapped)


def test_disabled_setup_replies_instead_of_silently_ignoring_owner(state, monkeypatch):
    db,calls,api,inventory=state
    inventory['telegram_reboot_enabled']=False;vps.save(inventory)
    reboot=MagicMock();monkeypatch.setattr(vps,'reboot',reboot)
    menu.process(db,SETTINGS,message(),api,NOW)
    assert 'setup did not finish' in calls[-1][1]['text']
    assert db.execute('SELECT count(*) FROM reboot_menus').fetchone()[0]==0
    reboot.assert_not_called()


def test_command_health_records_successful_getupdates_without_an_incoming_command(state, tmp_path):
    db,_,_,_=state
    api=MagicMock(return_value=[])
    menu.tick(db,SETTINGS,api,tmp_path/'lock',NOW)
    row=db.execute('SELECT * FROM bot_health').fetchone()
    assert row['last_poll']==NOW and row['error_code']==''
    assert api.call_args.args[1]=='getUpdates'


def test_status_hides_secrets_and_reports_unfinished_reboot_keys(state, monkeypatch, capsys):
    monkeypatch.setattr(tg,'config',lambda:SETTINGS)
    tg.status()
    output=capsys.readouterr().out
    assert 'Bot: @alertsbot' in output
    assert 'REBOOT BLOCKED' in output and 'NOT READY' in output
    assert 'private-token' not in output


@pytest.mark.parametrize('failed_step', ['credential', 'key', 'import'])
def test_manager_reports_failed_stage_without_secrets(state, monkeypatch, failed_step):
    _, _, _, settings = state
    settings['telegram_reboot_enabled'] = False
    vps.save(settings)
    monkeypatch.setattr(vps, 'migrate', lambda _: None)
    monkeypatch.setattr(vps, 'add', lambda _: None)
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['secret-command'], output='private-password', stderr='private-token')
    monkeypatch.setattr(vps, 'credential', fail if failed_step == 'credential' else lambda _: {})
    monkeypatch.setattr(vps, 'setup_key', fail if failed_step == 'key' else lambda *args: None)
    monkeypatch.setattr(vps, 'import_value', fail if failed_step == 'import' else lambda *args: None)
    with pytest.raises(SystemExit) as result:
        vps.main()
    text = str(result.value)
    expected = {'credential':'exporting saved SSH credential for "Server 1"',
                'key':'installing restricted reboot key for "Server 1"', 'import':'importing weekly workflow'}
    assert expected[failed_step] in text
    assert 'exit=1' in text
    assert not any(secret in text for secret in ('private-password', 'private-token', 'secret-command'))
    assert vps.load()['telegram_reboot_enabled'] is False


@pytest.mark.parametrize('code', ['SSH_AUTHENTICATION', 'SSH_HOST_KEY', 'private-password'])
def test_key_setup_only_exposes_fixed_diagnostics(state, monkeypatch, code):
    _, _, _, settings = state
    vps.KEYS.mkdir()
    key = vps.KEYS/server(1)['id']
    key.write_text('private-test-key')
    key.with_suffix('.pub').write_text('ssh-ed25519 YWJj test')
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['node'], output=json.dumps({'setup_error':code}))
    monkeypatch.setattr(vps, 'docker', fail)
    with pytest.raises(ValueError if code != 'private-password' else subprocess.CalledProcessError) as result:
        vps.setup_key(server(1), {'host':'192.0.2.1','port':22,'username':'root'})
    if code != 'private-password':
        assert str(result.value) == code
    else:
        assert 'private-password' not in str(result.value)


def test_setup_helper_disables_stdout_preload_only_for_its_own_process(state, monkeypatch):
    vps.KEYS.mkdir()
    key = vps.KEYS/server(1)['id']
    key.write_text('private-test-key')
    key.with_suffix('.pub').write_text('ssh-ed25519 YWJj test')
    calls = []
    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        return MagicMock(stdout=json.dumps({'host_key':'ssh-ed25519 YWJj'}))
    monkeypatch.setattr(vps, 'docker', docker)
    item = server(1)
    vps.setup_key(item, {'host':'192.0.2.1', 'port':22, 'username':'root', 'password':'secret'})
    args, kwargs = calls[0]
    assert args[:7] == ('exec', '-T', '-e', 'NODE_OPTIONS=', 'n8n', 'node', '-e')
    assert 'secret' not in str(args)
    assert item['reboot_ready'] is True
    assert (vps.KEYS/(item['id']+'.known_hosts')).read_text() == '192.0.2.1 ssh-ed25519 YWJj\n'
    assert 'NODE_OPTIONS=' not in Path('compose.yml').read_text()
