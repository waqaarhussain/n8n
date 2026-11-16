from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar
import app
import event_rules as rules
import school_history

START = (datetime.now(app.LONDON) + timedelta(days=20)).replace(hour=16, minute=0, second=0, microsecond=0)


def event(title='Winter Fair', **overrides):
    values = {'title': title, 'start': START.isoformat(), 'end': (START + timedelta(minutes=90)).isoformat(),
              'all_day': False, 'location': '', 'summary': 'All children must be accompanied by an adult.',
              'evidence': title, 'assumptions': [], 'identity_title': title, 'identity_evidence': title,
              'audiences': ['whole_school'], 'audience_evidence': ''}
    values.update(overrides)
    return app.ExtractedEvent(**values)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    client, calendar, remote = MagicMock(), MagicMock(), MagicMock()
    calendar.url = 'https://example.com/school/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    saves = []
    def remote_event(**kwargs):
        saves.append(kwargs)
        return remote
    monkeypatch.setattr(app.caldav, 'Event', remote_event)
    return saves


def process_events(monkeypatch, events, subject):
    extraction = app.Extraction(needs_review=False, reason='', events=events)
    monkeypatch.setattr(app, 'extract', lambda *args: extraction)
    body = '\n\n'.join(e.evidence + '\n' + e.identity_evidence + '\n' + e.audience_evidence + '\n' + e.summary for e in events)
    return app.process({'subject': subject, 'textPlain': body}, {'icloud': {'calendar_url': 'https://example.com/school/'}})


def test_fair_and_grotto_plus_mufti_create_two_entries_in_either_order(backend, monkeypatch):
    fair = event()
    mufti = event('Mufti Day', start=(START.date() - timedelta(days=7)).isoformat(), end=None,
                  all_day=True, summary='Non-uniform. Donations for the raffle please.')
    grotto = event("Santa's Magical Grotto at the Winter Fayre", identity_title='Winter Fayre',
                    identity_evidence='Winter Fayre', summary='Tickets £6.50 per child. Children under 5 must be accompanied. Buy before 25 November.')
    first = process_events(monkeypatch, [mufti, fair], 'Mufti Day / Winter Fair')
    second = process_events(monkeypatch, [grotto], 'Grotto visit')
    assert first['events_created'] == 2
    assert second['events_created'] == 0 and second['events_updated'] == 1
    assert second['duplicates_matched'] == 1
    with app.database() as db:
        assert db.execute('SELECT count(*) FROM event_index').fetchone()[0] == 2
        fair_record = db.execute('SELECT event_json FROM event_index WHERE day=?', (START.date().isoformat(),)).fetchone()
    notes = json.loads(fair_record[0])['summary']
    assert '£6.50' in notes and 'adult' in notes and '25 November' in notes
    assert backend[-1]['url'] == backend[1]['url']
    # Reverse arrival order must produce the same two-event result.
    with app.database() as db:
        for table in ('mail', 'events', 'event_index', 'event_aliases'): db.execute('DELETE FROM ' + table)
        db.commit()
    process_events(monkeypatch, [grotto], 'Grotto first')
    result = process_events(monkeypatch, [mufti, fair], 'Fair later')
    assert result['events_created'] == 1
    assert result['duplicates_matched'] == 1
    with app.database() as db:
        assert db.execute('SELECT count(*) FROM event_index').fetchone()[0] == 2


def test_same_email_duplicate_occurrences_are_planned_before_calendar_writes(backend, monkeypatch):
    fair = event()
    repeat = event('The Winter Fayre', identity_title='Winter Fayre', identity_evidence='Winter Fayre', summary='Wear uniform.')
    result = process_events(monkeypatch, [fair, repeat], 'Combined flyer')
    assert result['events_created'] == 1
    assert result['duplicates_matched'] == 1
    assert len(backend) == 1
    notes = str(Calendar.from_ical(backend[0]['data']).walk('VEVENT')[0]['description'])
    assert 'adult' in notes and 'Wear uniform' in notes


def test_same_time_different_activities_do_not_merge(backend, monkeypatch):
    process_events(monkeypatch, [event()], 'Fair')
    compare = MagicMock(return_value=rules.Decision(relationship='distinct', match_uid=None, confidence='high',
                                                   canonical_title='', reason='Different activities.'))
    monkeypatch.setattr(rules, 'compare', compare)
    result = process_events(monkeypatch, [event('Reception parents meeting', audiences=['reception'])], 'Meeting')
    assert result['events_created'] == 1
    assert compare.call_count == 1
    assert backend[0]['url'] != backend[1]['url']


def test_different_title_same_actual_event_uses_semantic_comparison(backend, monkeypatch):
    original = event("Santa's Magical Grotto", identity_title="Santa's Magical Grotto")
    process_events(monkeypatch, [original], 'First')
    with app.database() as db: uid = db.execute('SELECT uid FROM event_index').fetchone()[0]
    decision = rules.Decision(relationship='same_parent_event', match_uid=uid, confidence='high',
                              canonical_title='Winter Fair', reason='The grotto is at this fair for the same whole-event window.')
    monkeypatch.setattr(rules, 'compare', lambda *args: decision)
    result = process_events(monkeypatch, [event()], 'Winter Fair reminder')
    assert result['events_created'] == 0 and result['duplicates_matched'] == 1
    assert backend[-1]['url'] == backend[0]['url']


def test_separate_grotto_appointment_is_not_collapsed_into_whole_fair(backend, monkeypatch):
    process_events(monkeypatch, [event()], 'Fair')
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='distinct', match_uid=None,
        confidence='high', canonical_title='', reason='Separate appointment slot.'))
    appointment = event('Grotto appointment', end=(START + timedelta(minutes=15)).isoformat())
    result = process_events(monkeypatch, [appointment], 'Booked visit')
    assert result['events_created'] == 1
    assert backend[-1]['url'] != backend[0]['url']


def test_uncertain_match_requires_review_without_writing(backend, monkeypatch):
    process_events(monkeypatch, [event()], 'Fair')
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='uncertain', match_uid=None,
        confidence='low', canonical_title='', reason='Insufficient relationship evidence.'))
    result = process_events(monkeypatch, [event('Christmas visit')], 'Visit')
    assert result['status'] == 'review'
    assert len(backend) == 1


def test_mixed_newsletter_filters_each_event_and_keeps_unspecified(backend, monkeypatch):
    nursery = event('Nursery walk', audiences=['NuRsErY'], audience_evidence='Nursery children')
    reception = event('Reception workshop', audiences=['RECEPTION'])
    preprep = event('Pre-Prep sports day', audiences=['Pre-Prep'])
    year3 = event('Year 3 trip', audiences=['year_3'], audience_evidence='Year 3 only')
    unlabelled = event('School photo day', audiences=['unspecified'])
    # Different start days prevent unrelated semantic candidate checks in this test.
    events = [e.model_copy(update={'start': (START + timedelta(days=i)).isoformat(),
              'end': (START + timedelta(days=i, minutes=90)).isoformat()})
              for i, e in enumerate([nursery, reception, preprep, year3, unlabelled])]
    result = process_events(monkeypatch, events, 'Newsletter')
    assert result['events_created'] == 4
    assert len(result['filtered_events']) == 1
    assert result['filtered_events'][0]['title'] == 'Year 3 trip'


@pytest.mark.parametrize('label', ['Year 1', 'YEAR 2', 'Year 3', 'Year 4', 'Year 5', 'Year 6', 'Years 1-6', 'Years 1–6'])
def test_explicit_other_years_are_excluded_even_if_ai_says_whole_school(label):
    e = event(label + ' activity', audiences=['whole_school'], audience_evidence=label + ' only')
    assert rules.audience_result(e, {})[0] == 'ignore'


@pytest.mark.parametrize('label', ['nursery', 'Nursery', 'NURSERY', 'reception', 'Reception', 'Pre-Prep', 'pre prep', 'preprep'])
def test_allowed_cohort_spellings(label):
    assert rules.audience_result(event(label + ' activity', audiences=[label]), {})[0] == 'keep'


def test_future_year_group_configuration_is_applied_per_event():
    settings = {'school_audience': {'groups': ['year_1', 'reception'], 'include_whole_school': True}}
    assert rules.audience_result(event('Year 1 trip', audiences=['year_1']), settings)[0] == 'keep'
    assert rules.audience_result(event('Nursery walk', audiences=['nursery']), settings)[0] == 'ignore'
    assert rules.audience_result(event('Parent meeting', audiences=['unspecified']), settings)[0] == 'keep'


def test_metadata_evidence_must_come_from_actual_source(backend, monkeypatch):
    e = event()
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[e]))
    result = app.process({'subject': 'School', 'textPlain': 'Nothing about this fair.'}, {})
    assert result['status'] == 'review'
    assert not backend


def test_strict_provider_schema_requires_all_event_metadata_without_defaults():
    schema = rules.schema_for(app.Extraction)
    child = schema['$defs']['ExtractedEvent']
    assert set(child['required']) == set(child['properties'])
    assert 'identity_evidence' in child['required'] and 'audiences' in child['required']
    assert 'default' not in child['properties']['identity_title']


def test_reset_is_backed_up_and_leaves_duplicates_enabled_and_settings_alone(backend, monkeypatch, tmp_path):
    process_events(monkeypatch, [event()], 'Fair')
    monkeypatch.setattr(school_history, 'STATE', app.STATE)
    school_history.reset_history()
    with app.database() as db:
        for table in ('mail', 'events', 'event_index', 'event_aliases'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    backup = next(tmp_path.glob('school-history-before-reset-*.sqlite3'))
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT count(*) FROM event_index').fetchone()[0] == 1
    assert rules.policy({})['groups'] == sorted(rules.DEFAULT_GROUPS)


def test_audience_change_invalidates_extraction_cache_but_not_existing_event_identity(backend, monkeypatch):
    extraction = app.Extraction(needs_review=False, reason='', events=[event('Parent meeting', audiences=['unspecified'])])
    mock = MagicMock(return_value=extraction)
    monkeypatch.setattr(app, 'extract', mock)
    payload = {'subject': 'Parent meeting', 'textPlain': 'Details about the Parent meeting.'}
    first = app.process(payload, {})
    same = app.process(payload, {})
    changed = app.process(payload, {'school_audience': {'groups': ['year_1', 'reception']}})
    assert mock.call_count == 2
    assert first['events_created'] == 1
    assert same['events_created'] == changed['events_created'] == 0
    assert len(backend) == 1


def test_separate_cohorts_with_identical_generic_title_and_time_keep_separate_uids(backend, monkeypatch):
    first = event('School workshop', audiences=['nursery'])
    second = event('School workshop', audiences=['reception'])
    process_events(monkeypatch, [first], 'Nursery version')
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='distinct', match_uid=None,
        confidence='high', canonical_title='', reason='Separate cohort sessions.'))
    result = process_events(monkeypatch, [second], 'Reception version')
    assert result['events_created'] == 1
    assert backend[-1]['url'] != backend[0]['url']


def test_excluded_years_with_unusable_event_date_do_not_block_relevant_events(backend, monkeypatch):
    relevant = event('Nursery walk', audiences=['nursery'])
    ignored = event('Year 3 trip', audiences=['year_3'], start='unclear',
                    evidence='irrelevant malformed date', identity_evidence='not extracted properly')
    result = process_events(monkeypatch, [relevant, ignored], 'Mixed newsletter')
    assert result['status'] == 'processed'
    assert result['events_created'] == 1
    assert len(result['filtered_events']) == 1


def test_duplicate_comparator_sends_context_and_a_strict_schema(monkeypatch):
    incoming = event('Winter Fair')
    previous = event("Santa's Magical Grotto")
    body = rules.Decision(relationship='same_parent_event', match_uid='known', confidence='high',
                          canonical_title='Winter Fair', reason='Same fair occurrence.').model_dump_json()
    response = MagicMock(status_code=200)
    response.json.return_value = {'choices': [{'message': {'content': body}}]}
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(rules.httpx, 'Client', lambda **kwargs: client)
    result = rules.compare({'ai': {'api_key': 'test-key', 'model': 'gpt-6.1-sol'}}, incoming, [('known', previous)])
    sent = client.post.call_args.kwargs['json']
    assert sent['model'] == 'gpt-6.1-sol'
    assert sent['response_format']['json_schema']['strict'] is True
    facts = json.loads(sent['messages'][1]['content'])
    assert facts['candidates'][0]['uid'] == 'known'
    assert facts['incoming']['start'] == incoming.start
    assert 'location' not in facts['incoming']
    assert 'location' not in facts['candidates'][0]['event']
    assert result.match_uid == 'known'


def test_multiple_strong_candidates_are_reviewed_instead_of_picking_first(backend, monkeypatch):
    process_events(monkeypatch, [event(audiences=['nursery'])], 'Nursery session')
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='distinct', match_uid=None,
        confidence='high', canonical_title='', reason='Separate cohort sessions.'))
    process_events(monkeypatch, [event(audiences=['reception'])], 'Reception session')
    comparison = MagicMock(return_value=rules.Decision(relationship='uncertain', match_uid=None,
        confidence='low', canonical_title='', reason='Two possible cohort sessions.'))
    monkeypatch.setattr(rules, 'compare', comparison)
    result = process_events(monkeypatch, [event(audiences=['nursery', 'reception'])], 'Combined cohort reminder')
    assert result['status'] == 'review'
    assert comparison.call_count == 1
    assert len(backend) == 2


def test_nativity_reminder_refines_default_duration_despite_legacy_school_location(backend, monkeypatch):
    original = event('Nursery and Reception Nativity', end=None, location='In school',
                     audiences=['nursery', 'reception'], summary='Save the date for the Nativity.')
    process_events(monkeypatch, [original], 'Save the date')
    with app.database() as db:
        row = db.execute('SELECT calendar_key,uid,event_json FROM event_index').fetchone()
        legacy = json.loads(row['event_json'])
        legacy['location'] = 'In school'
        db.execute('UPDATE event_index SET event_json=? WHERE uid=?', (json.dumps(legacy), row['uid']))
        db.commit()
        uid = row['uid']
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='same_event', match_uid=uid,
        confidence='high', canonical_title='Nursery and Reception Nativity', reason='Same performance reminder.'))
    reminder = event('Nursery and Reception Nativity Play', location='School hall, The Shrubbery School',
                     end=(START + timedelta(minutes=30)).isoformat(), audiences=['nursery', 'reception'],
                     summary='The performance lasts approximately 30 minutes.')
    result = process_events(monkeypatch, [reminder], 'Nativity reminder')
    assert result['status'] == 'processed'
    assert result['events_created'] == 0 and result['events_updated'] == 1
    assert result['duplicates_matched'] == 1
    assert backend[-1]['url'] == backend[0]['url']
    ical = Calendar.from_ical(backend[-1]['data']).walk('VEVENT')[0]
    assert ical.decoded('dtend') - ical.decoded('dtstart') == timedelta(minutes=30)
    assert 'LOCATION' not in ical
    with app.database() as db:
        saved = json.loads(db.execute('SELECT event_json FROM event_index WHERE uid=?', (uid,)).fetchone()[0])
        assert 'location' not in saved


def test_location_is_not_requested_from_ai_or_included_in_calendar():
    schema = rules.schema_for(app.Extraction)['$defs']['ExtractedEvent']
    assert 'location' not in schema['properties']
    assert 'location' not in schema['required']
    old = event(location='School hall')
    assert 'location' not in old.model_dump()
    _, ics = app.prepare_event(old)
    assert 'LOCATION' not in Calendar.from_ical(ics).walk('VEVENT')[0]


def test_location_does_not_change_duplicate_signature():
    assert rules.signature(event(location='In school')) == rules.signature(event(location='School hall'))


def test_explicit_conflicting_end_times_still_require_review(backend, monkeypatch):
    process_events(monkeypatch, [event(location='In school')], 'First time')
    with app.database() as db: uid = db.execute('SELECT uid FROM event_index').fetchone()[0]
    monkeypatch.setattr(rules, 'compare', lambda *args: rules.Decision(relationship='same_event', match_uid=uid,
        confidence='high', canonical_title='Winter Fair', reason='Possible same event.'))
    changed = event(end=(START + timedelta(minutes=30)).isoformat(), location='School hall')
    result = process_events(monkeypatch, [changed], 'Conflicting explicit end')
    assert result['status'] == 'review'
    assert 'conflicting' in result['reason'] and 'times' in result['reason']
    assert len(backend) == 1
