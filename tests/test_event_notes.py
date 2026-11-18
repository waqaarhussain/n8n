"""Meaning-based notes reconciliation with verbatim provenance and atomic writes."""
from datetime import datetime, timedelta
import json
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar

import app
import event_notes as notes
import event_rules as rules
from test_event_rules import event


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    client, calendar = MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    saves = []
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', lambda **kw: saves.append(kw) or MagicMock())
    return saves


def process(monkeypatch, item, subject):
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[item]))
    body = '\n\n'.join([item.evidence, item.identity_evidence, item.summary])
    return app.process({'subject': subject, 'textPlain': body, 'date': '2026-11-18T11:00:00Z'}, {})


def decision(*paragraphs, review=False):
    return notes.NotesDecision(needs_review=review, reason='Supported instructions.',
        paragraphs=[notes.Paragraph(source=source, text=text) for source, text in paragraphs])


def test_reworded_closure_reminder_does_not_write_duplicate_notes(backend, monkeypatch):
    original = event('Club early closure', summary='Advance notice - on Friday 11th December, Club will be available until only 4.00pm.')
    process(monkeypatch, original, 'Closure')
    compare = MagicMock(return_value=decision(('saved', original.summary)))
    monkeypatch.setattr(notes, 'decide', compare)
    reminder = original.model_copy(update={'summary': 'Please note 11.12.26 Club is available until 4.00 pm.'})
    result = process(monkeypatch, reminder, 'Closure reminder')
    assert result['duplicates_matched'] == 1
    assert result['events_updated'] == result['events_created'] == 0
    assert len(backend) == 1
    compare.assert_called_once()
    with app.database() as db:
        assert json.loads(db.execute('SELECT event_json FROM event_index').fetchone()[0])['summary'] == original.summary


def test_next_mufti_reminder_cleans_legacy_repeats_and_retains_missing_biscuits(backend, monkeypatch):
    full = 'Non-Uniform. Donations for Raffle/Tombola Please. Bottles, Chocolates, Biscuits and Toys.'
    repeated = 'Donations for Raffle/Tombola Please. Bottles, Chocolates, Biscuits and Toys.'
    saved = event('Mufti Day', summary=full+'\n\n'+repeated)
    process(monkeypatch, saved, 'Original mufti notice')
    incoming = saved.model_copy(update={'summary': 'A quick reminder Friday is mufti day (non-uniform). Please send in donations of bottles, chocolates, toys, any raffle donations/tombola.'})
    monkeypatch.setattr(notes, 'decide', lambda *args: decision(('saved', full)))
    result = process(monkeypatch, incoming, 'Mufti reminder')
    assert result['events_updated'] == 1 and result['events_created'] == 0
    assert backend[0]['url'] == backend[1]['url']
    description = str(Calendar.from_ical(backend[-1]['data']).walk('VEVENT')[0]['DESCRIPTION'])
    assert description == full and 'Biscuits' in description


def test_new_fact_is_added_once_without_repeating_whole_reminder(backend, monkeypatch):
    saved = event('Mufti Day', summary='Non-uniform. Bring bottles, chocolates, biscuits and toys.')
    process(monkeypatch, saved, 'First')
    incoming = saved.model_copy(update={'summary': 'Reminder: non-uniform and donations. Leave donations at reception by 9am.'})
    monkeypatch.setattr(notes, 'decide', lambda *args: decision(('saved', saved.summary),
        ('incoming', 'Leave donations at reception by 9am.')))
    result = process(monkeypatch, incoming, 'New collection instruction')
    assert result['events_updated'] == 1
    description = str(Calendar.from_ical(backend[-1]['data']).walk('VEVENT')[0]['DESCRIPTION'])
    assert description == saved.summary + '\n\nLeave donations at reception by 9am.'
    assert description.count('Non-uniform') == 1


def test_explicit_correction_replaces_old_instruction_and_preserves_other_detail(backend, monkeypatch):
    old_detail = 'Wear non-uniform. Collection is at the main gate.'
    saved = event('Mufti Day', summary=old_detail+'\n\nBring bottles for the tombola.')
    process(monkeypatch, saved, 'First')
    correction = 'Correction: no bottles for the tombola; please bring books instead.'
    incoming = saved.model_copy(update={'summary': correction})
    monkeypatch.setattr(notes, 'decide', lambda *args: decision(('saved', old_detail), ('incoming', correction)))
    assert process(monkeypatch, incoming, 'Correction')['events_updated'] == 1
    description = str(Calendar.from_ical(backend[-1]['data']).walk('VEVENT')[0]['DESCRIPTION'])
    assert old_detail in description and correction in description
    assert 'Bring bottles for' not in description


@pytest.mark.parametrize('mode', ['conflict', 'invented', 'empty', 'unavailable'])
def test_unsafe_comparison_keeps_old_calendar_and_index_untouched(backend, monkeypatch, mode):
    saved = event(summary='Wear uniform. Bring biscuits.')
    process(monkeypatch, saved, 'First')
    if mode == 'conflict': result = decision(review=True)
    elif mode == 'invented': result = decision(('incoming', 'Bring £100 in cash.'))
    elif mode == 'empty': result = decision()
    else: result = None
    mock = MagicMock(return_value=result)
    if mode == 'unavailable': mock.side_effect = ValueError('Notes provider unavailable.')
    monkeypatch.setattr(notes, 'decide', mock)
    incoming = saved.model_copy(update={'summary': 'Wear non-uniform. Bring books.'})
    assert process(monkeypatch, incoming, 'Conflicting reminder')['status'] == 'review'
    assert len(backend) == 1
    with app.database() as db:
        assert json.loads(db.execute('SELECT event_json FROM event_index').fetchone()[0])['summary'] == saved.summary


def test_cached_pair_reuses_notes_decision_and_context_changes_invalidate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    first = event(summary='Club closes at 4pm.')
    second = event(summary='Club is available until 16:00.')
    model = MagicMock(return_value=decision(('saved', first.summary)))
    monkeypatch.setattr(notes, 'decide', model)
    with app.database() as db:
        for _ in range(2):
            assert notes.reconcile(db, {}, first, second, {'thread_text':'same notice'}) == first.summary
        assert model.call_count == 1
        notes.reconcile(db, {}, first, second, {'thread_text':'different chronology'})
        assert model.call_count == 2


def test_identical_empty_and_exact_duplicate_paragraphs_skip_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    model = MagicMock()
    monkeypatch.setattr(notes, 'decide', model)
    with app.database() as db:
        assert notes.reconcile(db, {}, event(summary='Bring biscuits.\n\nBring biscuits.'), event(summary='Bring biscuits.')) == 'Bring biscuits.'
        assert notes.reconcile(db, {}, event(summary=''), event(summary='Bring books.')) == 'Bring books.'
        assert notes.reconcile(db, {}, event(summary='Bring books.'), event(summary='')) == 'Bring books.'
    model.assert_not_called()


def test_prompt_requires_meaning_unique_details_explicit_corrections_and_provenance(monkeypatch):
    body = decision(('saved', 'Bring biscuits.')).model_dump_json()
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = app.httpx.Response(200, json={'choices':[{'message':{'content':body}}]})
    monkeypatch.setattr(notes.httpx, 'Client', lambda **kw: client)
    result = notes.decide({'ai':{'api_key':'test'}}, event(summary='Bring biscuits.'), event(summary='Reminder: bring biscuits.'), {'thread_text':'From: Old letter'})
    request = client.post.call_args.kwargs['json']
    prompt = request['messages'][0]['content']
    assert 'Compare MEANING' in prompt and 'Omission is\nnot withdrawal' in prompt
    assert 'CONTIGUOUS EXACT excerpt' in prompt and 'Incoming was\nprocessed later' in prompt
    assert request['response_format']['json_schema']['strict'] is True
    assert json.loads(request['messages'][1]['content'])['thread_context']['thread_text'] == 'From: Old letter'
    assert result.paragraphs[0].source == 'saved'


def test_entire_batch_stays_unwritten_when_later_notes_conflict(backend, monkeypatch):
    saved = event(summary='Wear uniform.')
    process(monkeypatch, saved, 'First')
    fresh = event('Nursery trip', start=(datetime.fromisoformat(saved.start)+timedelta(days=1)).isoformat(),
        end=None, summary='Bring a packed lunch.')
    incoming = saved.model_copy(update={'summary':'Wear non-uniform.'})
    monkeypatch.setattr(notes, 'decide', lambda *args: decision(review=True))
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[fresh,incoming]))
    body = '\n'.join(e.evidence+'\n'+e.identity_evidence for e in (fresh,incoming))
    assert app.process({'subject':'Newsletter', 'textPlain':body}, {})['status'] == 'review'
    assert len(backend) == 1
