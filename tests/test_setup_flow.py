import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock
import sys


def wizard():
    spec = importlib.util.spec_from_file_location('setup_flow', Path(__file__).resolve().parents[1] / 'bridge/setup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_setup_uses_fixed_defaults_without_calendar_selection(tmp_path, monkeypatch, capsys):
    setup = wizard()
    path = tmp_path / 'settings.json'
    monkeypatch.setattr(setup, 'CONFIG', path)
    monkeypatch.setattr(setup.os, 'chown', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['setup.py'])
    prompts = []
    def ask(label, old=''):
        prompts.append(label)
        return old
    monkeypatch.setattr(setup, 'ask', ask)
    monkeypatch.setattr(setup, 'secret', lambda label, old='', preview=False: 'sk-test-key-12345678' if preview else 'apple-password')
    client = MagicMock()
    monkeypatch.setattr(setup.caldav, 'DAVClient', lambda **kwargs: client)
    connection = MagicMock()
    connection.select.return_value = ('OK', [])
    monkeypatch.setattr(setup.imaplib, 'IMAP4_SSL', lambda *a, **kw: connection)
    setup.main()
    saved = json.loads(path.read_text())
    assert saved['ai']['model'] == 'gpt-6.1-sol'
    assert saved['mail']['username'] == 'hackdroid@icloud.com'
    assert saved['mail']['alias'] == 'waqaarn8n@icloud.com'
    assert 'calendar_url' not in saved['icloud']
    assert prompts == ['Apple Account email']
    client.principal.return_value.calendars.assert_not_called()
    connection.login.assert_called_once_with('hackdroid@icloud.com', 'apple-password')
    assert saved['ai']['api_key'] not in capsys.readouterr().out


def test_import_calendar_selection_preserves_shared_keys(tmp_path, monkeypatch):
    setup = wizard()
    path = tmp_path / 'settings.json'
    original = {'icloud': {'username': 'apple@example.com', 'password': 'password'},
                'ai': {'api_key': 'private-key'}, 'mail': {'alias': 'alias'}}
    path.write_text(json.dumps(original))
    monkeypatch.setattr(setup, 'CONFIG', path)
    monkeypatch.setattr(setup.os, 'chown', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--select-calendar'])
    monkeypatch.setattr(setup, 'ask', lambda *a: '2')
    calendars = [MagicMock(), MagicMock()]
    calendars[1].name = 'Family'
    calendars[1].url = 'https://example.com/family/'
    client = MagicMock()
    client.principal.return_value.calendars.return_value = calendars
    monkeypatch.setattr(setup.caldav, 'DAVClient', lambda **kw: client)
    setup.main()
    saved = json.loads(path.read_text())
    assert saved['icloud']['calendar_url'] == calendars[1].url
    assert saved['ai'] == original['ai']
    assert saved['mail'] == original['mail']
    client.close.assert_called_once()


def test_live_key_preview_never_echoes_whole_paste(monkeypatch, capsys):
    setup = wizard()
    stdin = MagicMock()
    stdin.isatty.return_value = True
    stdin.fileno.return_value = 123
    monkeypatch.setattr(setup.sys, 'stdin', stdin)
    monkeypatch.setattr(setup.termios, 'tcgetattr', lambda _: [0, 0, 0, 0, 0, 0, [0] * 32])
    restore = MagicMock()
    monkeypatch.setattr(setup.termios, 'tcsetattr', restore)
    key = 'sk-example-very-private-9876'
    data = iter((key + '\n').encode())
    monkeypatch.setattr(setup.os, 'read', lambda *args: bytes([next(data)]))
    assert setup.preview_input('Key: ') == key
    out = capsys.readouterr().out
    assert key not in out
    assert 'sk-e…9876' in out
    assert restore.call_count == 2
