import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import app
import attachments
from document_smoke import docx_bytes


def test_docx_attachment_only_message_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    extraction = MagicMock(return_value=app.Extraction(needs_review=False, reason='No events', events=[]))
    monkeypatch.setattr(app, 'extract', extraction)
    result = app.process({'subject': 'See attached invitation', 'body': '', 'attachments': [{
        'filename': 'invite.docx', 'data': base64.b64encode(docx_bytes()).decode()}]}, {})
    assert result['status'] == 'processed'
    sent_body = extraction.call_args.args[2]
    assert '10 December 2026 at 2.15pm' in sent_body and 'End time 3.30pm' in sent_body


def test_bad_or_unsupported_attachment_blocks_calendar_before_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    extract, calendar = MagicMock(), MagicMock()
    monkeypatch.setattr(app, 'extract', extract)
    monkeypatch.setattr(app, 'connect_calendar', calendar)
    for filename in ('broken.pdf', 'sheet.xlsx'):
        result = app.process({'subject': 'School', 'body': 'Invite', 'attachments': [{
            'filename': filename, 'data': 'not valid base64'}]}, {})
        assert result['status'] == 'review' and result['events_created'] == 0
    extract.assert_not_called()
    calendar.assert_not_called()


def test_attachment_changes_invalidate_email_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    monkeypatch.setattr(attachments, 'extract_attachments', lambda data, **kwargs: (data['test_document'], []))
    extract = MagicMock(return_value=app.Extraction(needs_review=False, reason='No events', events=[]))
    monkeypatch.setattr(app, 'extract', extract)
    for document in ('First invitation', 'Second invitation'):
        app.process({'subject': 'School', 'body': 'See attached.', 'test_document': document}, {})
    assert extract.call_count == 2


def test_importable_workflow_reads_binary_attachments_safely():
    workflow = json.loads((Path(__file__).resolve().parents[1]/'workflows/school-icloud.json').read_text())
    assert workflow['nodes'][0]['parameters']['downloadAttachments'] is True
    code = next(n for n in workflow['nodes'] if n['type'] == 'n8n-nodes-base.code')
    assert 'this.helpers.getBinaryDataBuffer' in code['parameters']['jsCode']
    assert workflow['connections']['Watch iCloud school alias']['main'][0][0]['node'] == code['name']


def test_saved_legacy_event_notes_are_refreshed_without_new_uid(tmp_path, monkeypatch):
    from test_bridge import example
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    event = example(assumptions=['Year inferred'])
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[event]))
    uid, _ = app.prepare_event(event)
    with app.database() as db:
        db.execute('INSERT INTO events (uid, created_at) VALUES (?, ?)', (uid, 'old'))
        db.commit()
    calendar, client, remote = MagicMock(), MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', remote)
    app.process({'subject': 'School', 'body': 'Workshop at 2.15pm'}, {})
    saved = remote.call_args.kwargs['data']
    assert 'Assumptions' not in saved and 'Year inferred' not in saved
    from urllib.parse import unquote
    assert unquote(remote.call_args.kwargs['url']).endswith(uid + '.ics')


def test_description_contains_only_practical_summary():
    from test_bridge import example
    event = example(summary='Wear school uniform. Collection is at the main gate.')
    _, raw = app.prepare_event(event)
    item = app.Calendar.from_ical(raw).walk('VEVENT')[0]
    assert str(item['description']) == event.summary
    assert 'Evidence:' not in str(item['description']) and 'Source:' not in str(item['description'])


def test_generic_email_and_image_text_reach_model_together(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    monkeypatch.setattr(attachments, 'extract_attachments', lambda data, **kwargs: (
        'Attachment: winter-fair.jpg\nWinter Fair 27 November 2026. In uniform. 4-5.30pm.', []))
    model = MagicMock(return_value=app.Extraction(needs_review=False, reason='', events=[]))
    monkeypatch.setattr(app, 'extract', model)
    app.process({'subject': 'Flyer', 'body': 'FYI please see attached.'}, {})
    assert model.call_args.args[1] == 'Flyer'
    body = model.call_args.args[2]
    assert 'FYI please see attached.' in body and 'Winter Fair' in body and 'In uniform' in body


def test_legacy_model_cache_refreshes_once_under_new_extraction_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    data = {'subject': 'Flyer', 'body': 'Please see attached.'}
    extraction = app.Extraction(needs_review=False, reason='', events=[])
    identity = app.fingerprint(data['subject'], data['body'])
    with app.database() as db:
        db.execute('INSERT INTO mail (fingerprint, extraction) VALUES (?, ?)', (identity, extraction.model_dump_json()))
        db.commit()
    model = MagicMock(return_value=extraction)
    monkeypatch.setattr(app, 'extract', model)
    app.process(data, {})
    app.process(data, {})
    assert model.call_count == 1


def test_date_only_school_visit_keeps_full_useful_body_as_all_day(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from test_bridge import example
    monkeypatch.setattr(app, 'STATE', tmp_path/'state.sqlite3')
    day = (datetime.now(app.LONDON) + timedelta(days=3)).date()
    text = 'Pre-prep, Nursery and Reception will have a visit from Aston Villa Tots in the afternoon for a PE and football skills workshop. Please send your child in her PE kit.'
    event = example(title='Aston Villa Tots visit', start=day.isoformat(), end=None,
                    all_day=True, summary=text, evidence='Aston Villa Tots')
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False, reason='', events=[event]))
    client, calendar, remote = MagicMock(), MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client, calendar))
    monkeypatch.setattr(app.caldav, 'Event', remote)
    result = app.process({'subject': 'Aston Villa Tots visit', 'body': text}, {})
    assert result['events_created'] == 1
    saved = app.Calendar.from_ical(remote.call_args.kwargs['data']).walk('VEVENT')[0]
    assert saved.decoded('dtstart') == day
    assert saved.decoded('dtend') == day + timedelta(days=1)
    assert str(saved['description']) == text
