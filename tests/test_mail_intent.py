from datetime import datetime, timedelta
from unittest.mock import MagicMock
import json

import pytest
import app
from event_rules import schema_for


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    calendar = MagicMock()
    monkeypatch.setattr(app, 'connect_calendar', calendar)
    return calendar


@pytest.mark.parametrize(('intent', 'body'), [
    ('conversation', "Thanks, we'll both be there! Haha, hope the singing is better than mine.\n\nFrom: School\nSubject: Nativity\nPlease see the attached parent information letter for 2 December."),
    ('conversation', 'Your two places are reserved. See you then!'),
    ('general_advice', 'Please send children in warm coats and hats during this cold weather.'),
    ('no_event', 'The newsletter website has a new layout. Thank you for your feedback.'),
])
def test_non_actionable_messages_finish_successfully_even_if_old_letter_caused_review(backend, monkeypatch, intent, body):
    extraction = app.Extraction(intent=intent, actionable_evidence='', needs_review=True,
        reason='No new calendar action in the current message; the quoted old letter is not attached.', events=[])
    model = MagicMock(return_value=extraction)
    monkeypatch.setattr(app, 'extract', model)
    data = {'subject': 'Re: Nursery and Reception Nativity', 'textPlain': body}
    result = app.process(data, {})
    assert result['status'] == 'ignored'
    assert result['intent'] == intent
    assert result['reason'] == app.NO_ACTION_REASONS[intent]
    assert result['events_created'] == result['events_updated'] == 0
    assert result['notification']['status'] == 'disabled'
    assert result['uncertain_deliveries'] == 0
    assert app.process(data, {})['status'] == 'ignored'
    assert model.call_count == 1
    backend.assert_not_called()
    # No-action mail never reaches the calendar index or notification sender.
    with app.database() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0


def test_conversation_label_cannot_hide_extracted_events(backend, monkeypatch):
    from test_bridge import example
    extracted = app.Extraction(intent='conversation', actionable_evidence='', needs_review=False,
        reason='', events=[example()])
    monkeypatch.setattr(app, 'extract', lambda *args: extracted)
    result = app.process({'subject': 'School', 'textPlain': 'New calendar information.'}, {})
    assert result['status'] == 'review'
    assert 'intent conflicts' in result['reason']
    backend.assert_not_called()


def test_general_advice_label_cannot_hide_new_action_evidence(backend, monkeypatch):
    extracted = app.Extraction(intent='general_advice', actionable_evidence='The date has changed.',
        needs_review=False, reason='', events=[])
    monkeypatch.setattr(app, 'extract', lambda *args: extracted)
    result = app.process({'subject': 'School', 'textPlain': 'The date has changed.'}, {})
    assert result['status'] == 'review'
    backend.assert_not_called()


@pytest.mark.parametrize('intent', ['event_notice', 'event_update'])
def test_real_missing_event_details_and_changes_still_require_review(backend, monkeypatch, intent):
    extracted = app.Extraction(intent=intent, actionable_evidence='Please see the attached updated invitation.',
        needs_review=True, reason='The current event invitation is missing.', events=[])
    monkeypatch.setattr(app, 'extract', lambda *args: extracted)
    result = app.process({'subject': 'Nativity update', 'textPlain': 'Please see the attached updated invitation.'}, {})
    assert result['status'] == 'review'
    assert result['intent'] == intent
    backend.assert_not_called()


def test_new_dated_clothing_activity_still_creates_calendar_event(tmp_path, monkeypatch):
    from test_bridge import example
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    day = (datetime.now(app.LONDON) + timedelta(days=20)).date().isoformat()
    text = f'Nursery children should wear PE kit on {day} for sports day.'
    e = example(title='Nursery sports day', start=day, all_day=True, evidence=text)
    extraction = app.Extraction(intent='event_notice', actionable_evidence=text,
        needs_review=False, reason='', events=[e])
    monkeypatch.setattr(app, 'extract', lambda *args: extraction)
    client, calendar, remote = MagicMock(), MagicMock(), MagicMock()
    calendar.url = 'https://example.com/school/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', lambda **kw: remote)
    result = app.process({'subject': 'Sports day', 'textPlain': text}, {})
    assert result['status'] == 'processed'
    assert result['events_created'] == 1
    remote.save.assert_called_once()


def test_raw_thread_headers_are_supplied_without_changing_complete_event_body(backend, monkeypatch):
    model = MagicMock(return_value=app.Extraction(intent='conversation', actionable_evidence='',
        needs_review=False, reason='Ordinary reply.', events=[]))
    monkeypatch.setattr(app, 'extract', model)
    raw = 'Thanks, see you there!\n\nFrom: School\nSent: Yesterday\nSubject: Nativity\nPlease see the attached letter.'
    result = app.process({'subject': 'Re: Nativity', 'textPlain': raw}, {})
    assert result['status'] == 'ignored'
    args = model.call_args.args
    assert 'From: School' not in args[2]  # normal cleaned extraction body
    assert 'From: School' in args[4]      # supplemental reply-boundary context
    assert 'Thanks, see you there!' in args[2] and 'Please see the attached letter.' in args[2]


def test_html_quote_boundaries_survive_context_but_scripts_do_not():
    context = app.thread_context({'html': '<p>Haha, yes please!</p><blockquote><p>Old invitation</p></blockquote><script>private-script</script>'})
    assert '[QUOTED EARLIER MESSAGE]' in context
    assert '[END QUOTED MESSAGE]' in context
    assert 'Haha, yes please!' in context and 'Old invitation' in context
    assert 'private-script' not in context


def test_previous_review_cache_is_reclassified_once_after_version_update(backend, monkeypatch):
    version = app.EXTRACTION_VERSION
    data = {'subject': 'Re: Nativity', 'textPlain': 'Thanks! See you then. Quoted old letter below.'}
    old = app.Extraction(needs_review=True, reason='Referenced letter missing.', events=[])
    model = MagicMock(return_value=old)
    monkeypatch.setattr(app, 'extract', model)
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version - 1)
    assert app.process(data, {})['status'] == 'review'
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version)
    model.return_value = app.Extraction(intent='conversation', actionable_evidence='',
        needs_review=False, reason='An acknowledgement, not a new event.', events=[])
    assert app.process(data, {})['status'] == 'ignored'
    assert app.process(data, {})['status'] == 'ignored'
    assert model.call_count == 2
    backend.assert_not_called()


def test_intent_is_required_in_strict_provider_schema_and_thread_context_is_sent(monkeypatch):
    schema = schema_for(app.Extraction)
    assert 'intent' in schema['required']
    assert 'actionable_evidence' in schema['required']
    response = app.Extraction(intent='general_advice', actionable_evidence='', needs_review=False,
        reason='General weather advice.', events=[]).model_dump_json()
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = app.httpx.Response(200, json={'choices': [{'message': {'content': response}}]})
    monkeypatch.setattr(app.httpx, 'Client', lambda **kw: client)
    result = app.extract({'ai': {'api_key': 'test'}}, 'Warm clothing', 'Wear warm hats.', '2026-11-16',
                         'Wear warm hats.\n\nFrom: Earlier letter')
    sent = client.post.call_args.kwargs['json']
    assert json.loads(sent['messages'][1]['content'])['thread_text'].endswith('From: Earlier letter')
    assert 'Missing attachments referenced ONLY in old quoted messages' in sent['messages'][0]['content']
    assert result.intent == 'general_advice'


def test_provider_failure_is_not_relabelled_as_a_harmless_skip(backend, monkeypatch):
    monkeypatch.setattr(app, 'extract', MagicMock(side_effect=ValueError('AI provider unavailable.')))
    with pytest.raises(ValueError, match='AI provider unavailable'):
        app.process({'subject': 'Warm hats', 'textPlain': 'Please wear warm hats.'}, {})
    backend.assert_not_called()


def test_unreadable_current_attachment_still_requires_review(backend, monkeypatch):
    extractor = MagicMock()
    monkeypatch.setattr(app, 'extract', extractor)
    result = app.process({'subject': 'School', 'textPlain': 'Thanks, invitation attached.',
        'attachments': [{'filename': 'invitation.pdf', 'data': 'not-valid-base64'}]}, {})
    assert result['status'] == 'review'
    extractor.assert_not_called()
    backend.assert_not_called()
