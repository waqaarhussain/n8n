from unittest.mock import MagicMock
import pytest
import app
import textmebot


def test_request_uses_documented_recipient_key_and_text(monkeypatch):
    client = MagicMock()
    client.get.return_value = app.httpx.Response(200, json={'success': True})
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(textmebot.httpx, 'Client', lambda **kwargs: context)
    textmebot.send({'api_key': 'dummy-key', 'recipient': '+447123456789'}, 'Subject\n\nEmail body')
    client.get.assert_called_once_with('https://api.textmebot.com/send.php', params={
        'recipient': '+447123456789', 'apikey': 'dummy-key', 'text': 'Subject\n\nEmail body'})


def test_provider_errors_hide_secret(monkeypatch):
    client = MagicMock()
    key = 'dummy-secret-123'
    client.get.return_value = app.httpx.Response(403, json={'error': 'Invalid key ' + key})
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(textmebot.httpx, 'Client', lambda **kwargs: context)
    with pytest.raises(textmebot.DeliveryError) as caught:
        textmebot.send({'api_key': key, 'recipient': '+447123456789'}, 'Test')
    assert key not in str(caught.value) and '403' in str(caught.value)


def test_failed_notification_does_not_undo_calendar_and_is_not_resent(tmp_path, monkeypatch):
    from test_bridge import example
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[example()]))
    client, calendar = MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    remote = MagicMock()
    monkeypatch.setattr(app.caldav, 'Event', remote)
    sender = MagicMock(side_effect=textmebot.DeliveryError('Provider timeout'))
    monkeypatch.setattr(textmebot, 'send', sender)
    settings = {'textmebot': {'enabled': True, 'api_key': 'dummy-key', 'recipient': '+447123456789'}}
    data = {'subject': 'School', 'body': 'Workshop at 2.15pm'}
    first = app.process(data, settings)
    assert first['status'] == 'processed' and first['events_created'] == 1
    assert first['notification']['status'] == 'uncertain'
    assert first['uncertain_deliveries'] == 0
    second = app.process(data, settings)
    assert second['events_created'] == 0 and second['notification']['status'] == 'held'
    assert sender.call_count == 1 and remote.return_value.save.call_count == 1


def test_body_cleanup_and_split_preserve_links():
    body = 'Good afternoon.\nPlease complete the survey.\nhttps://example.com/survey\nKind regards.\nThe Shrubbery School Limited\nLegal disclaimer'
    cleaned = textmebot.clean_body(body)
    assert cleaned == 'Good afternoon.\nPlease complete the survey.\nhttps://example.com/survey'
    url = 'https://example.com/' + 'a'*180
    text = 'Hello. '*25 + url + ' Please respond.'
    pieces = textmebot.parts(text, 350)
    assert ''.join(pieces) == text and any(url in part for part in pieces)
