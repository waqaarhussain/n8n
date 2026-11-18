"""Dated operating-hours notices are calendar reminders, not blanket review cases."""
from datetime import datetime
import json
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar

import app
import event_rules

SUBJECT = 'After-school Club Early Closure on Friday 11th December 2026'
BODY = ('Advance notice - on Friday 11th December, the last day of term, '
        'Club will be available until only 4.00pm.')


class ReferenceClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 18, 10, tzinfo=app.LONDON).astimezone(tz) if tz else cls(2026, 11, 18, 10)


def closure(**changes):
    data = dict(title='After-school Club closes at 4pm', identity_title='After-school Club closes at 4pm',
                identity_evidence=SUBJECT, start='2026-12-11', end=None, all_day=True,
                summary=BODY, evidence=BODY, assumptions=[], audiences=['unspecified'], audience_evidence='')
    data.update(changes)
    return app.ExtractedEvent(**data)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    monkeypatch.setattr(app, 'datetime', ReferenceClock)
    client, calendar = MagicMock(), MagicMock()
    calendar.url = 'https://example.com/school/'
    connection = MagicMock(return_value=(client, calendar))
    monkeypatch.setattr(app, 'connect_calendar', connection)
    saved = []
    monkeypatch.setattr(app.caldav, 'Event', lambda **kwargs: saved.append(kwargs['data']) or MagicMock())
    return saved, connection


def test_closure_is_saved_once_with_cutoff_not_invented_hours(backend, monkeypatch):
    extracted = app.Extraction(intent='event_notice', actionable_evidence=BODY,
        needs_review=False, reason='', events=[closure()])
    model = MagicMock(return_value=extracted)
    monkeypatch.setattr(app, 'extract', model)
    data = {'subject': SUBJECT, 'textPlain': BODY, 'date': '2026-11-17T16:05:00+00:00'}
    result = app.process(data, {})
    assert result['status'] == 'processed'
    assert result['events_created'] == 1
    saved, connection = backend
    event = Calendar.from_ical(saved[0]).walk('VEVENT')[0]
    assert str(event['SUMMARY']) == 'After-school Club closes at 4pm'
    assert event.decoded('DTSTART').isoformat() == '2026-12-11'
    assert event.decoded('DTEND').isoformat() == '2026-12-12'
    assert str(event['DESCRIPTION']) == BODY
    # A repeat notice reuses the existing resource without an extra write or model call.
    assert app.process(data, {})['events_created'] == 0
    assert len(saved) == 1 and model.call_count == 1


def test_old_blanket_change_review_cache_is_reextracted_without_history_reset(backend, monkeypatch):
    data = {'subject': SUBJECT, 'textPlain': BODY}
    version = app.EXTRACTION_VERSION
    model = MagicMock(return_value=app.Extraction(intent='event_update', actionable_evidence=BODY,
        needs_review=True, reason='Early closure changes availability; manual review required.', events=[]))
    monkeypatch.setattr(app, 'extract', model)
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version - 1)
    assert app.process(data, {})['status'] == 'review'
    monkeypatch.setattr(app, 'EXTRACTION_VERSION', version)
    model.return_value = app.Extraction(intent='event_notice', actionable_evidence=BODY,
        needs_review=False, reason='', events=[closure()])
    result = app.process(data, {})
    assert result['status'] != 'review' and result['events_created'] == 1
    assert model.call_count == 2


@pytest.mark.parametrize('reason', ['Closing date is ambiguous.', 'Club session has been cancelled.',
                                   'Two sources give conflicting collection times.'])
def test_genuine_review_not_overridden_by_closure_words(backend, monkeypatch, reason):
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(intent='event_update',
        actionable_evidence=BODY, needs_review=True, reason=reason, events=[]))
    result = app.process({'subject': SUBJECT, 'textPlain': BODY}, {})
    assert result['status'] == 'review' and result['reason'] == reason
    backend[1].assert_not_called()


def test_closure_does_not_merge_into_last_day_of_term_marker(backend, monkeypatch):
    term = closure(title='Last day of term', identity_title='', identity_evidence='', summary='Term ends.')
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[term]))
    assert app.process({'subject': 'Last day of term', 'textPlain': BODY}, {})['events_created'] == 1
    decision = MagicMock(return_value=event_rules.Decision(relationship='distinct', confidence='high',
        match_uid='', canonical_title='', reason='Closure reminder and term ending are different activities.'))
    monkeypatch.setattr(event_rules, 'compare', decision)
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[closure()]))
    assert app.process({'subject': SUBJECT, 'textPlain': BODY}, {})['events_created'] == 1
    assert len(backend[0]) == 2
    decision.assert_called_once()


def test_model_receives_explicit_closure_policy_without_weakening_reschedule_review(monkeypatch):
    answer = app.Extraction(intent='event_notice', actionable_evidence=BODY,
        needs_review=False, reason='', events=[closure()])
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = app.httpx.Response(200, json={'choices': [{'message': {'content': answer.model_dump_json()}}]})
    monkeypatch.setattr(app.httpx, 'Client', lambda **kw: client)
    result = app.extract({'ai': {'api_key': 'test'}}, SUBJECT, BODY, '2026-11-17')
    prompt = client.post.call_args.kwargs['json']['messages'][0]['content']
    assert 'Operating-hours and early-closure notices are actionable dated reminders' in prompt
    assert 'A closing/collection cutoff is NOT an activity start time' in prompt
    assert 'include the explicit closing time in BOTH its title and summary' in prompt
    assert 'A cancelled or rescheduled existing event requires manual review' in prompt
    assert result.events[0].all_day is True and result.events[0].end is None
