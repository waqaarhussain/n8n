import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('telegram_alerts', ROOT/'scripts/telegram.py')
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, 'CONFIG', tmp_path/'config/telegram.json')
    monkeypatch.setattr(tg, 'STATE', tmp_path/'data/telegram.sqlite3')
    monkeypatch.setattr(tg, 'public_url', lambda: 'https://n8n.example.com')
    monkeypatch.setattr(tg.time, 'sleep', lambda _: None)
    db = tg.database()
    yield db
    db.close()


def event(identity='11', workflow='school'):
    return {'id': identity, 'workflow_id': workflow, 'name': 'School <email>', 'status': 'error',
            'started_at': '2026-11-14T22:00:00+00:00', 'stopped_at': '2026-11-14T22:00:12+00:00'}


def enable(db, monkeypatch, identity='school'):
    monkeypatch.setattr(tg, 'sql', lambda _: '2026-11-14T21:00:00+00:00')
    tg.set_subscription(db, identity, True)


def test_all_workflows_start_off_and_idle_monitor_does_not_query_n8n(state, monkeypatch):
    lookup = MagicMock()
    monkeypatch.setattr(tg, 'failed_executions', lookup)
    assert tg.tick(state, {'token': 'dummy', 'chat_id': '1'}) == 0
    assert state.execute('SELECT count(*) FROM subscriptions').fetchone()[0] == 0
    lookup.assert_not_called()


def test_alert_is_clean_escaped_and_links_to_execution():
    row = event()
    row['error'] = 'password=NEVER-SEND; private school email'
    payload = tg.alert_payload(row, '123', 'https://n8n.example.com')
    assert '<b>School &lt;email&gt;</b>' in payload['text']
    assert '14 Nov 2026, 22:00 GMT' in payload['text']
    assert 'NEVER-SEND' not in json.dumps(payload)
    assert payload['reply_markup']['inline_keyboard'][0][0]['url'] == 'https://n8n.example.com/workflow/school/executions/11'
    row['status'] = 'crashed'
    row['stopped_at'] = None
    assert 'Crashed' in tg.alert_payload(row, '123', 'https://n8n.example.com')['text']


def test_persistent_delivery_dedup_and_out_of_order_failures(state, monkeypatch):
    enable(state, monkeypatch)
    rows = [event('12')]
    monkeypatch.setattr(tg, 'failed_executions', lambda _: rows)
    send = MagicMock()
    monkeypatch.setattr(tg, 'api', send)
    settings = {'token': 'private', 'chat_id': '123'}
    assert tg.tick(state, settings, now=100) == 1
    assert tg.tick(state, settings, now=200) == 0
    # A long-running lower-ID execution can fail after a higher one. It is not lost.
    rows.insert(0, event('11'))
    assert tg.tick(state, settings, now=300) == 1
    assert send.call_count == 2
    reopened = tg.database()
    assert tg.tick(reopened, settings, now=400) == 0
    reopened.close()


def test_disabled_workflow_does_not_send_and_pending_waits(state, monkeypatch):
    enable(state, monkeypatch)
    def disable_mid_poll(_):
        tg.set_subscription(state, 'school', False)
        return [event()]
    monkeypatch.setattr(tg, 'failed_executions', disable_mid_poll)
    send = MagicMock()
    monkeypatch.setattr(tg, 'api', send)
    assert tg.tick(state, {'token': 'private', 'chat_id': '123'}) == 0
    send.assert_not_called()


def test_failed_send_is_retried_with_persistent_global_cooldown(state, monkeypatch):
    enable(state, monkeypatch)
    monkeypatch.setattr(tg, 'failed_executions', lambda _: [event('11'), event('12')])
    send = MagicMock(side_effect=tg.TelegramError(429, 120))
    monkeypatch.setattr(tg, 'api', send)
    settings = {'token': 'private', 'chat_id': '123'}
    assert tg.tick(state, settings, now=100) == 0
    assert send.call_count == 1
    assert tg.tick(state, settings, now=219) == 0
    assert send.call_count == 1
    send.side_effect = None
    assert tg.tick(state, settings, now=221) == 2
    assert state.execute('SELECT count(*) FROM deliveries WHERE sent=1').fetchone()[0] == 2


def test_only_opted_in_workflows_and_new_failed_runs_are_queried(monkeypatch):
    queries = []
    monkeypatch.setattr(tg, 'sql', lambda query: queries.append(query) or [])
    tg.failed_executions([{'workflow_id': "kid's", 'enabled_at': '2026-11-14T22:00:00+00:00'}])
    query = queries[0]
    assert "'kid''s'" in query
    assert 'e."createdAt">=' in query
    assert "('error','crashed')" in query
    assert 'e."deletedAt" IS NULL' in query
    assert 'execution_data' not in query
    assert 'UPDATE' not in query


def test_sql_is_read_only_and_keeps_database_errors_private(monkeypatch):
    run = MagicMock(return_value=MagicMock(stdout='[]'))
    monkeypatch.setattr(tg.subprocess, 'run', run)
    assert tg.sql('SELECT 1;') == []
    kwargs = run.call_args.kwargs
    assert kwargs['input'].startswith('BEGIN READ ONLY;')
    assert kwargs['stderr'] == tg.subprocess.PIPE


def test_setup_only_binds_exact_private_start_and_stores_no_default_opt_ins(state, monkeypatch):
    token = '123456:' + 'x'*30
    monkeypatch.setattr(tg.getpass, 'getpass', lambda _: token)
    monkeypatch.setattr(tg.secrets, 'token_hex', lambda _: 'nonce')
    monkeypatch.setattr('builtins.input', lambda _: '')
    calls = []
    def api(_token, method, payload=None):
        assert _token == token
        calls.append((method, payload))
        if method == 'getMe':
            return {'username': 'MyAlertsBot'}
        if method == 'getWebhookInfo':
            return {}
        if method == 'getUpdates':
            return [
                {'update_id': 1, 'message': {'text': '/start n8n_nonce', 'chat': {'id': 999, 'type': 'group'}}},
                {'update_id': 2, 'message': {'text': '/start other', 'chat': {'id': 777, 'type': 'private'}}},
                {'update_id': 3, 'message': {'text': '/start n8n_nonce', 'chat': {'id': 123, 'type': 'private'}}}]
        return {'message_id': 1}
    monkeypatch.setattr(tg, 'api', api)
    tg.setup()
    assert tg.config()['chat_id'] == '123'
    assert tg.CONFIG.stat().st_mode & 0o777 == 0o600
    assert state.execute('SELECT count(*) FROM subscriptions').fetchone()[0] == 0
    assert calls[-1][1]['chat_id'] == '123'


def test_rotating_token_keeps_workflow_choices_and_resets_delivery_cooldown(state, monkeypatch):
    enable(state, monkeypatch)
    state.execute('UPDATE delivery_control SET next_request=999999999999')
    state.commit()
    tg.save_config({'token': '123456:' + 'x'*30, 'chat_id': '123'})
    monkeypatch.setattr(tg.getpass, 'getpass', lambda _: '')
    monkeypatch.setattr(tg.secrets, 'token_hex', lambda _: 'nonce')
    monkeypatch.setattr('builtins.input', lambda _: '')
    def api(_token, method, payload=None):
        if method == 'getMe': return {'username': 'MyAlertsBot'}
        if method == 'getWebhookInfo': return {}
        if method == 'getUpdates': return [{'update_id': 1, 'message': {'text': '/start n8n_nonce', 'chat': {'id': 123, 'type': 'private'}}}]
        return {'message_id': 1}
    monkeypatch.setattr(tg, 'api', api)
    tg.setup()
    assert state.execute('SELECT enabled FROM subscriptions').fetchone()[0] == 1
    assert state.execute('SELECT next_request FROM delivery_control').fetchone()[0] == 0


def test_telegram_errors_never_include_token_url(monkeypatch):
    from urllib.error import URLError
    token = '123456:never-log-this'
    monkeypatch.setattr(tg, 'urlopen', MagicMock(side_effect=URLError('https://api.telegram.org/bot' + token)))
    with pytest.raises(tg.TelegramError) as exc:
        tg.api(token, 'sendMessage')
    assert token not in str(exc.value)
    assert 'api.telegram.org' not in str(exc.value)


def test_api_error_classifies_callback_expiry_without_exposing_description():
    error = tg.api_error('answerCallbackQuery', 400, {
        'description':'Bad Request: query is too old and response timeout expired or query ID is invalid private-token'})
    assert error.method == 'answerCallbackQuery'
    assert error.reason == 'CALLBACK_EXPIRED'
    assert 'private-token' not in str(vars(error))
