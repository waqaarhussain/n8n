import base64
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import pytest
import app
import vision

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4X8AAAAASUVORK5CYII=')


def client_for(monkeypatch, text='Winter Fayre\n27 November 2026\n4:00 - 5:30 pm\nTickets £6.50 per child'):
    client = MagicMock()
    client.post.return_value = app.httpx.Response(200, json={'choices': [{'message': {
        'content': app.json.dumps({'text': text, 'decorative': False})}}]})
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(vision.httpx, 'Client', lambda **kwargs: context)
    return client


def test_vision_sends_actual_image_bytes_and_caches_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    client = client_for(monkeypatch)
    settings = {'ai': {'api_key': 'test-key', 'model': 'gpt-6.1-sol'}}
    first = vision.transcribe(settings, PNG, 'poster.png')
    assert '27 November' in first and '£6.50' in first
    assert vision.transcribe(settings, PNG, 'same-image.png') == first
    assert client.post.call_count == 1
    payload = client.post.call_args.kwargs['json']
    image = payload['messages'][1]['content'][1]['image_url']
    assert image['url'] == 'data:image/png;base64,' + base64.b64encode(PNG).decode()
    assert image['detail'] == 'high' and payload['model'] == 'gpt-6.1-sol'


def test_model_rejection_is_review_not_silent_local_ocr_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    client = client_for(monkeypatch)
    client.post.return_value = app.httpx.Response(400, json={'error': 'Unsupported input'})
    extractor, calendar = MagicMock(), MagicMock()
    monkeypatch.setattr(app, 'extract', extractor)
    monkeypatch.setattr(app, 'connect_calendar', calendar)
    result = app.process({'subject': 'Flyer', 'body': 'Please see attached.', 'attachments': [
        {'filename': 'poster.png', 'data': base64.b64encode(PNG).decode()}]},
        {'ai': {'api_key': 'test-key'}})
    assert result['status'] == 'review' and 'HTTP 400' in result['reason']
    assert result['attachment_summary'][0]['reader'] == 'OpenAI image reading'
    extractor.assert_not_called()
    calendar.assert_not_called()


def test_vision_transcription_is_used_as_verifiable_event_source(tmp_path, monkeypatch):
    from test_bridge import example
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    date = (datetime.now(app.LONDON) + timedelta(days=30)).date()
    visible = f'Winter Fayre {date.isoformat()} 4:00 - 5:30 pm. Tickets £6.50 per child.'
    client_for(monkeypatch, visible)
    extractor = MagicMock(return_value=app.Extraction(needs_review=False, reason='', events=[
        example(title='Winter Fayre', start=date.isoformat(), all_day=True, evidence=visible)]))
    monkeypatch.setattr(app, 'extract', extractor)
    client, calendar, remote = MagicMock(), MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', remote)
    result = app.process({'subject': 'Flyer', 'body': 'Please see attached.', 'attachments': [
        {'filename': 'poster.png', 'data': base64.b64encode(PNG).decode()}]},
        {'ai': {'api_key': 'test-key'}})
    assert result['events_created'] == 1
    assert visible in extractor.call_args.args[2]
    remote.return_value.save.assert_called_once()
