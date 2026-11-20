"""Parent tasks and administrative deadlines are actionable calendar entries."""
from datetime import datetime
import json
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar

import app
import event_rules as rules
from test_event_rules import event

SUBJECT = 'Lunch Orders for Spring Term'
BODY = ('Please find attached a copy of our lunch menu for the Spring Term. '
        'Can you please take a look at the new menu and complete the attached order form, '
        'returning it to office@example.com by Thursday 3rd December.')
QUOTE = 'returning it to office@example.com by Thursday 3rd December.'


class ReferenceClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 20, 12, tzinfo=app.LONDON).astimezone(tz) if tz else cls(2026, 11, 20, 12)


def deadline(title='Return Spring Term lunch order form', **changes):
    data = dict(title=title, identity_title=title, identity_evidence='', entry_kind='deadline',
        start='2026-12-03', end=None, all_day=True, summary=BODY, evidence=QUOTE,
        assumptions=['Due year inferred from the November 2026 notice, not the Spring 2027 menu label.'],
        audiences=['unspecified'], audience_evidence='')
    # Identity must have source support; without a verbatim action title use title matching.
    data['identity_title'] = ''
    data.update(changes)
    return app.ExtractedEvent(**data)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    monkeypatch.setattr(app, 'datetime', ReferenceClock)
    client, calendar = MagicMock(), MagicMock()
    calendar.url = 'https://example.com/school/'
    saves = []
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', lambda **kw: saves.append(kw) or MagicMock())
    return saves


def process(monkeypatch, extracted, body=BODY, subject=SUBJECT):
    monkeypatch.setattr(app, 'extract', lambda *args: extracted)
    return app.process({'subject':subject, 'textPlain':body, 'date':'2026-11-20T12:20:00Z'}, {})


def result(*entries):
    return app.Extraction(intent='event_notice', actionable_evidence=QUOTE,
        needs_review=False, reason='', events=list(entries))


def test_lunch_form_deadline_saved_on_due_date_without_menu_events(backend, monkeypatch):
    # The body supplies the action/date; menu labels are context, not a due-year override.
    body = BODY + '\n\nAttachment: Spring Term 2027 menu. Monday: pasta. Tuesday: rice.'
    output = process(monkeypatch, result(deadline()), body)
    assert output['status'] == 'processed' and output['events_created'] == 1
    item = Calendar.from_ical(backend[0]['data']).walk('VEVENT')[0]
    assert str(item['SUMMARY']) == 'Return Spring Term lunch order form'
    assert item.decoded('DTSTART').isoformat() == '2026-12-03'
    assert item.decoded('DTEND').isoformat() == '2026-12-04'
    assert 'office@example.com' in str(item['DESCRIPTION'])
    with app.database() as db:
        entry = json.loads(db.execute('SELECT event_json FROM event_index').fetchone()[0])
        assert entry['entry_kind'] == 'deadline'


@pytest.mark.parametrize('title', ['Return consent form', 'Reply to Nativity invitation',
    'Pay school trip balance', 'Book Winter Fair tickets', 'Submit lunch choices'])
def test_other_parent_action_deadlines_remain_actionable(backend, monkeypatch, title):
    assert process(monkeypatch, result(deadline(title)))['events_created'] == 1


def test_deadline_repeat_reuses_uid_and_does_not_write_again(backend, monkeypatch):
    extracted = result(deadline())
    assert process(monkeypatch, extracted)['events_created'] == 1
    repeated = process(monkeypatch, extracted, subject='Reminder: lunch order deadline')
    assert repeated['duplicates_matched'] == 1
    assert repeated['events_created'] == repeated['events_updated'] == 0
    assert len(backend) == 1


def test_same_day_same_parent_title_activity_and_deadline_cannot_merge(backend, monkeypatch):
    activity = deadline('School activity', entry_kind='activity')
    assert process(monkeypatch, result(activity))['events_created'] == 1
    compare = MagicMock(side_effect=AssertionError('Cross-kind candidates must never reach AI comparison'))
    monkeypatch.setattr(rules, 'compare', compare)
    task = deadline('School activity')  # Even an AI title mistake must not erase the activity.
    output = process(monkeypatch, result(task), subject='Reply deadline')
    assert output['events_created'] == 1 and output['duplicates_matched'] == 0
    assert backend[0]['url'] != backend[1]['url']
    assert rules.signature(activity) != rules.signature(task)
    assert not rules.same_window(activity, task)
    compare.assert_not_called()


def test_activity_plus_earlier_rsvp_deadline_in_one_email_saves_both(backend, monkeypatch):
    activity = deadline('Nativity performance', entry_kind='activity', start='2026-12-10')
    task = deadline('Return Nativity ticket form')
    assert process(monkeypatch, result(activity, task))['events_created'] == 2
    assert len(backend) == 2


def test_cache_bump_reprocesses_previous_no_event_without_resetting_history(backend, monkeypatch):
    version = app.EXTRACTION_VERSION
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version - 1)
    ignored = app.Extraction(intent='no_event', actionable_evidence='', needs_review=False,
        reason='Administrative form, not an event.', events=[])
    assert process(monkeypatch, ignored)['status'] == 'ignored'
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version)
    assert process(monkeypatch, result(deadline()))['events_created'] == 1
    assert len(backend) == 1


def test_form_received_acknowledgement_and_undated_weather_advice_still_skip(backend, monkeypatch):
    for intent, body in [('conversation', 'Thanks, your lunch form has been received.'),
                         ('general_advice', 'Please wear warm hats during this cold weather.')]:
        skipped = app.Extraction(intent=intent, actionable_evidence='', needs_review=False, reason='', events=[])
        assert process(monkeypatch, skipped, body)['status'] == 'ignored'
    assert not backend


def test_other_year_deadline_filtered_per_task(backend, monkeypatch):
    task = deadline('Year 3 trip consent form due', audiences=['year_3'], audience_evidence='Year 3 only')
    output = process(monkeypatch, result(task), BODY+'\nYear 3 only')
    assert output['events_created'] == 0 and len(output['filtered_events']) == 1
    assert not backend


def test_ambiguous_deadline_still_requires_review(backend, monkeypatch):
    extracted = app.Extraction(intent='event_notice', actionable_evidence='Please return the form by Thursday.',
        needs_review=True, reason='Conflicting due dates in source documents.', events=[])
    assert process(monkeypatch, extracted)['status'] == 'review'
    assert not backend


def test_provider_prompt_includes_tasks_and_strict_kind_schema(monkeypatch):
    response = result(deadline()).model_dump_json()
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = app.httpx.Response(200, json={'choices':[{'message':{'content':response}}]})
    monkeypatch.setattr(app.httpx, 'Client', lambda **kw: client)
    app.extract({'ai':{'api_key':'test'}}, SUBJECT, BODY, '2026-11-20')
    request = client.post.call_args.kwargs['json']
    prompt = request['messages'][0]['content']
    assert 'actionable dated parent tasks/deadlines' in prompt
    assert '2026-12-03 deadline' in prompt and 'Spring 2027' in prompt
    assert 'Never use an RSVP deadline as the main event' not in prompt
    assert 'not email timestamps or RSVP/payment deadlines' not in prompt
    schema = request['response_format']['json_schema']['schema']['$defs']['ExtractedEvent']
    assert 'entry_kind' in schema['required']
    assert schema['properties']['entry_kind']['enum'] == ['activity', 'deadline']


def test_legacy_saved_events_default_to_activity():
    old = event().model_dump()
    old.pop('entry_kind')
    assert app.ExtractedEvent.model_validate(old).entry_kind == 'activity'


def test_different_tasks_due_same_day_are_checked_as_distinct(backend, monkeypatch):
    first = deadline('Return lunch choices')
    assert process(monkeypatch, result(first))['events_created'] == 1
    compare = MagicMock(return_value=rules.Decision(relationship='distinct', match_uid=None,
        confidence='high', canonical_title='', reason='Different required parent actions.'))
    monkeypatch.setattr(rules, 'compare', compare)
    second = deadline('Pay trip balance')
    output = process(monkeypatch, result(second), subject='Trip payment deadline')
    assert output['events_created'] == 1 and output['duplicates_matched'] == 0
    assert len(backend) == 2 and backend[0]['url'] != backend[1]['url']
    compare.assert_called_once()


def test_reworded_deadline_title_can_match_same_action(backend, monkeypatch):
    first = deadline()
    assert process(monkeypatch, result(first))['events_created'] == 1
    with app.database() as db:
        uid = db.execute('SELECT uid FROM event_index').fetchone()[0]
    compare = MagicMock(return_value=rules.Decision(relationship='same_event', match_uid=uid,
        confidence='high', canonical_title=first.title, reason='Same term, form and due date.'))
    monkeypatch.setattr(rules, 'compare', compare)
    second = deadline('Spring lunch choices due')
    output = process(monkeypatch, result(second), subject='Reminder')
    assert output['events_created'] == 0 and output['duplicates_matched'] == 1
    assert len(backend) == 1
