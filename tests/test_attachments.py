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
    monkeypatch.setattr(attachments, 'extract_attachments', lambda data: (data['test_document'], []))
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
