import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vps_tg', ROOT/'scripts/telegram.py')
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)
WF = json.loads((ROOT/'workflows/vps-weekly-updates.json').read_text())


def flatten(value):
    table = []
    def intern(v):
        index = len(table)
        table.append(None)
        if isinstance(v, dict):
            table[index] = {k: intern(x) if isinstance(x, (str, dict, list)) else x for k, x in v.items()}
        elif isinstance(v, list):
            table[index] = [intern(x) if isinstance(x, (str, dict, list)) else x for x in v]
        else:
            table[index] = v
        return str(index)
    intern(value)
    return json.dumps(table)


def summary(name='My VPS', reboot=True):
    return {'resultData': {'runData': {'VPS reboot summary': [{'data': {'main': [[{'json': {
        'maintenance_version': 1, 'vps_name': name, 'reboot_required': reboot, 'update_status': 'success'
    }}]]}}]}}}


@pytest.mark.parametrize('name', ['My VPS', '123', 'Media & Kids'])
def test_only_saved_display_name_in_reboot_graph(name):
    graph = summary(name)
    graph['resultData']['runData']['SSH node'] = [{'hostname': 'ubuntu-random', 'stdout': 'PRIVATE'}]
    assert tg.reboot_names(flatten(graph)) == [name]
    assert tg.reboot_names(flatten(summary(name, False))) == []


@pytest.mark.parametrize('name', ['bad\nname', '', 'x'*101])
def test_invalid_summary_names_rejected(name):
    with pytest.raises(ValueError):
        tg.reboot_names(flatten(summary(name)))


def test_reboot_alert_opt_in_new_success_only(monkeypatch):
    lookup = MagicMock(return_value=[])
    monkeypatch.setattr(tg, 'sql', lookup)
    assert tg.reboot_executions([{'workflow_id': 'school'}]) == []
    lookup.assert_not_called()
    lookup.return_value = [{'id': '99', 'workflow_id': tg.MAINTENANCE_WORKFLOW, 'data': flatten(summary())}]
    rows = tg.reboot_executions([{'workflow_id': tg.MAINTENANCE_WORKFLOW, 'enabled_at': '2026-11-16T00:00Z'}])
    assert rows == [{'id': 'reboot:99', 'workflow_id': tg.MAINTENANCE_WORKFLOW, 'reboot_names': ['My VPS']}]
    query = lookup.call_args.args[0]
    assert "e.status='success'" in query and 'e."createdAt">=' in query and 'e."deletedAt" IS NULL' in query


def test_reboot_delivery_retries_and_never_relabels_school_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, 'STATE', tmp_path/'tg.db')
    monkeypatch.setattr(tg, 'public_url', lambda: 'https://n8n.example.com')
    monkeypatch.setattr(tg, 'sql', lambda _: '2026-11-16T00:00:00Z')
    monkeypatch.setattr(tg.time, 'sleep', lambda _: None)
    db = tg.database()
    tg.set_subscription(db, tg.MAINTENANCE_WORKFLOW, True)
    monkeypatch.setattr(tg, 'failed_executions', lambda _: [])
    monkeypatch.setattr(tg, 'reboot_executions', lambda _: [{'id': 'reboot:99', 'workflow_id': tg.MAINTENANCE_WORKFLOW, 'reboot_names': ['Media VPS', 'Kids VPS']}])
    send = MagicMock(side_effect=tg.TelegramError(429, 120))
    monkeypatch.setattr(tg, 'api', send)
    settings = {'token': 'private', 'chat_id': '123'}
    assert tg.tick(db, settings, now=100) == 0
    assert tg.tick(db, settings, now=200) == 0
    send.side_effect = None
    assert tg.tick(db, settings, now=221) == 1
    assert send.call_args.args[2] == {'chat_id': '123', 'text': 'Media VPS requires reboot\nKids VPS requires reboot'}
    assert tg.tick(db, settings, now=300) == 0
    db.close()


def test_workflow_schedule_credential_and_failure_validation():
    assert WF['active'] is False
    assert WF['settings']['timezone'] == 'Europe/London'
    assert WF['settings']['saveDataSuccessExecution'] == 'all'
    assert WF['nodes'][0]['parameters']['rule']['interval'][0]['expression'] == '0 0 * * 1'
    ssh = WF['nodes'][3]
    assert ssh['type'] == 'n8n-nodes-base.ssh'
    assert ssh['parameters']['authentication'] == 'password'
    command = ssh['parameters']['command']
    assert 'apt-get' in command and ' upgrade -y' in command
    assert 'APT::Update::Error-Mode=any' in command
    assert '/var/run/reboot-required' in command
    assert not any(line.strip().startswith(('reboot', 'shutdown')) for line in command.splitlines())
    code = WF['nodes'][4]['parameters']['jsCode']
    script = '''const assert = require('node:assert/strict');
const run = new Function('$input', '$', CODE);
const name = () => ({first: () => ({json: {vps_name: 'Chosen VPS'}})});
const input = (code, stdout) => ({first: () => ({json: {code, stdout}})});
assert.equal(run(input(0, 'N8N_REBOOT_REQUIRED=1'), name)[0].json.reboot_required, true);
assert.equal(run(input(0, 'N8N_REBOOT_REQUIRED=0'), name)[0].json.reboot_required, false);
assert.equal(run(input(0, 'N8N_REBOOT_REQUIRED=1'), name)[0].json.vps_name, 'Chosen VPS');
assert.throws(() => run(input(100, 'N8N_REBOOT_REQUIRED=1'), name));
assert.throws(() => run(input(null, 'N8N_REBOOT_REQUIRED=1'), name));
assert.throws(() => run(input(0, ''), name));
assert.throws(() => run(input(0, 'N8N_REBOOT_REQUIRED=0\\nN8N_REBOOT_REQUIRED=1'), name));
'''.replace('CODE', json.dumps(code))
    subprocess.run(['node', '-e', script], check=True)


@pytest.mark.parametrize('fail_at', ['', 'update', 'upgrade'])
def test_remote_update_script_exit_and_order(tmp_path, fail_at):
    # Run exactly the generated shell program with fake apt/flock/id binaries.
    # The system package manager is never invoked by tests.
    import os
    bindir = tmp_path/'bin'
    bindir.mkdir()
    log = tmp_path/'calls'
    for name, script in {
        'id': 'echo 0', 'flock': 'exit 0',
        'apt-get': 'echo "$*" >> "$CALLS"; case "$*" in *"$FAIL_AT"*) [ -z "$FAIL_AT" ] || exit 100;; esac',
    }.items():
        p = bindir/name
        p.write_text('#!/bin/sh\n' + script + '\n')
        p.chmod(0o755)
    command = WF['nodes'][3]['parameters']['command'].replace('/var/lock/n8n-weekly-apt.lock', str(tmp_path/'lock')).replace('/var/run/reboot-required', str(tmp_path/'reboot-required'))
    (tmp_path/'reboot-required').touch()
    proc = subprocess.run(['bash', '-c', command], env={**os.environ, 'PATH': str(bindir)+':'+os.environ['PATH'], 'CALLS': str(log), 'FAIL_AT': fail_at}, capture_output=True, text=True)
    assert proc.returncode == (100 if fail_at else 0)
    lines = log.read_text().splitlines()
    assert ' update' in lines[0]
    assert len(lines) == (1 if fail_at == 'update' else 2)
    assert ('N8N_REBOOT_REQUIRED=1' in proc.stdout) == (not fail_at)


def test_malformed_saved_summary_does_not_block_other_alerts(monkeypatch):
    monkeypatch.setattr(tg, 'sql', lambda _: [
        {'id': '1', 'data': '[]'},
        {'id': '2', 'workflow_id': tg.MAINTENANCE_WORKFLOW, 'data': flatten(summary())},
    ])
    rows = tg.reboot_executions([{'workflow_id': tg.MAINTENANCE_WORKFLOW, 'enabled_at': '2026-11-16T00:00Z'}])
    assert len(rows) == 1 and rows[0]['id'] == 'reboot:2'


def test_importer_keeps_password_out_of_workflow_and_uses_private_host(monkeypatch):
    spec = importlib.util.spec_from_file_location('import_vps', ROOT/'scripts/import-vps.py')
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    monkeypatch.chdir(ROOT)
    answers = iter(['192.0.2.42', "Media 'VPS'"])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(importer.getpass, 'getpass', lambda _: 'private-test-password')
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return MagicMock(stdout='0')
    monkeypatch.setattr(importer, 'run', run)
    importer.main()
    submitted = [json.loads(kw['input']) for args, kw in calls if 'input' in kw]
    assert submitted[0][0]['data']['host'] == '192.0.2.42'
    assert submitted[0][0]['data']['username'] == 'root'
    assert submitted[0][0]['data']['password'] == 'private-test-password'
    workflow = submitted[1]
    assert workflow['active'] is False
    assert 'private-test-password' not in json.dumps(workflow)
    assert "Media 'VPS'" in workflow['nodes'][2]['parameters']['jsCode']
    assert all('private-test-password' not in str(args) for args, kw in calls)
    assert calls[-1][0][-1] == '/tmp/waqaar-vps-workflow.json'
