import json
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
import app


def example(**changes):
    start = (datetime.now(timezone.utc) + timedelta(days=30)).astimezone(app.LONDON).replace(hour=14, minute=15, second=0, microsecond=0)
    data = {'title':'Reception Gingerbread Workshop','start':start.isoformat(),'end':None,
            'all_day':False,'location':'School','summary':'RSVP before 1 December.',
            'evidence':'Workshop at 2.15pm', 'assumptions':[]}
    data.update(changes)
    return app.ExtractedEvent(**data)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', tmp_path / 'state.sqlite3')


def test_forwarded_html_headers_removed_preserves_prose():
    title, body = app.clean_email('Fwd: Re: Workshop', '''<div>Begin forwarded message:</div>
<div><b>From:</b> school@example.com</div><div>To: parent@example.com</div>
<div>Reply-To: teacher@example.com</div><div>Date: yesterday</div>
<div>Subject: Workshop</div><div>Good morning Mr Hussain</div>
<p>Please join us on 10th December at 2.15pm.</p><p>Reply before 1st December.</p>''', True)
    assert title == 'Workshop'
    assert 'example.com' not in body
    assert 'Good morning Mr Hussain' in body
    assert '10th December at 2.15pm' in body
    assert 'Reply before 1st December.' in body


def test_full_outlook_html_body_used_not_preview():
    title, body, _ = app.normalise_email({'subject':'School','bodyPreview':'short',
        'body':{'contentType':'html','content':'<p>Full invitation</p>'}})
    assert body == 'Full invitation'
    with pytest.raises(ValueError):
        app.normalise_email({'subject':'School','bodyPreview':'short'})


def test_content_duplicate_ignores_forward_prefix_and_spacing():
    one = app.clean_email('Fwd: Workshop', 'Text\nwith spaces')
    two = app.clean_email('Workshop', 'Text with   spaces')
    assert app.fingerprint(*one) == app.fingerprint(*two)


def test_missing_end_explicitly_labelled_and_duration_correct():
    uid, raw = app.prepare_event(example())
    event = app.Calendar.from_ical(raw).walk('VEVENT')[0]
    assert event.decoded('dtend') - event.decoded('dtstart') == timedelta(minutes=60)
    assert 'placeholder' in str(event['description'])
    assert uid == app.prepare_event(example())[0]


def test_actual_end_used():
    event = example()
    end = datetime.fromisoformat(event.start) + timedelta(minutes=95)
    _, raw = app.prepare_event(event.model_copy(update={'end':end.isoformat()}))
    item = app.Calendar.from_ical(raw).walk('VEVENT')[0]
    assert item.decoded('dtend') - item.decoded('dtstart') == timedelta(minutes=95)
    assert 'placeholder' not in str(item['description'])


def test_invalid_range_and_timezone_rejected():
    event = example()
    with pytest.raises(ValueError):
        app.prepare_event(event.model_copy(update={'end':event.start}))
    with pytest.raises(ValueError):
        app.timed('2027-07-01T14:00:00+00:00')
    with pytest.raises(ValueError):
        app.timed('2027-12-01T14:00:00')
    assert app.timed('2027-07-01T14:00:00+01:00').hour == 14


def test_all_day_exclusive_end():
    day = (datetime.now(app.LONDON) + timedelta(days=30)).date()
    _, raw = app.prepare_event(example(start=day.isoformat(), all_day=True))
    item = app.Calendar.from_ical(raw).walk('VEVENT')[0]
    assert item.decoded('dtstart') == day
    assert item.decoded('dtend') == day + timedelta(days=1)


def test_chunks_preserve_entire_body():
    text = 'Email From School\n\n' + ('word line\n' * 1000)
    parts = app.chunks(text)
    assert ''.join(parts) == text
    assert max(map(len, parts)) <= 350


def test_two_recipients_duplicate_email_no_resend(state, monkeypatch):
    sent = []
    monkeypatch.setattr(app, 'send_inout', lambda key, text: sent.append((key,text)))
    settings = {'inout':{'recipients':[{'api_key':'one'},{'api_key':'two'}]}}
    with app.database() as db:
        first = app.notify(db, settings, 'id', 'Workshop','Body')
        second = app.notify(db, settings, 'id', 'Workshop','Body')
    assert first == (2,0)
    assert second == (0,0)
    assert [key for key,_ in sent] == ['one','two']


def test_ambiguous_send_not_automatically_retried(state, monkeypatch):
    send = MagicMock(side_effect=TimeoutError)
    monkeypatch.setattr(app, 'send_inout', send)
    settings={'inout':{'recipients':[{'api_key':'one'}]}}
    with app.database() as db:
        assert app.notify(db, settings, 'id','Title','Body') == (0,1)
        assert app.notify(db, settings, 'id','Title','Body') == (0,1)
    assert send.call_count == 1


def test_review_no_calendar_write_but_email_still_sent(state, monkeypatch):
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=True, reason='Conflicting dates', events=[]))
    connect = MagicMock()
    monkeypatch.setattr(app, 'connect_calendar', connect)
    monkeypatch.setattr(app, 'notify', lambda *args:(1,0))
    result=app.process({'subject':'School','body':'Conflicting dates'}, {})
    assert result['status']=='review'
    connect.assert_not_called()


def test_idempotent_event_and_cached_ai(state, monkeypatch):
    extract = MagicMock(return_value=app.Extraction(needs_review=False,reason='',events=[example()]))
    monkeypatch.setattr(app, 'extract', extract)
    client, calendar = MagicMock(), MagicMock()
    calendar.url = 'https://example.com/calendar/'
    monkeypatch.setattr(app, 'connect_calendar', lambda _: (client,calendar))
    remote = MagicMock()
    monkeypatch.setattr(app.caldav, 'Event', remote)
    monkeypatch.setattr(app, 'notify', lambda *args:(0,0))
    data={'subject':'School','body':'Workshop at 2.15pm'}
    first=app.process(data,{})
    second=app.process(data,{})
    assert first['events_created']==1
    assert second['events_created']==0
    assert second['status']=='duplicate'
    assert extract.call_count==1
    assert remote.return_value.save.call_count==1


def test_fabricated_evidence_blocks_calendar(state, monkeypatch):
    monkeypatch.setattr(app, 'extract', lambda *args: app.Extraction(needs_review=False,reason='',events=[example(evidence='not in email')]))
    connect=MagicMock()
    monkeypatch.setattr(app, 'connect_calendar',connect)
    monkeypatch.setattr(app, 'notify', lambda *args:(0,0))
    assert app.process({'subject':'School','body':'Workshop at 2.15pm'}, {})['status']=='review'
    connect.assert_not_called()


def test_wrong_calendar_never_falls_back(monkeypatch):
    client=MagicMock()
    calendar=MagicMock()
    calendar.url='https://example.com/not-selected/'
    client.principal.return_value.calendars.return_value=[calendar]
    monkeypatch.setattr(app.caldav,'DAVClient',lambda **kwargs:client)
    with pytest.raises(ValueError):
        app.connect_calendar({'icloud':{'username':'x','password':'x','calendar_url':'https://example.com/selected/'}})
    client.close.assert_called_once()


def test_workflow_folder_placeholder_and_raw():
    workflow=json.loads((Path(__file__).resolve().parents[1]/'workflows/school-outlook.json').read_text())
    node=workflow['nodes'][0]
    assert node['parameters']['output']=='raw'
    assert node['parameters']['filters']['readStatus']=='both'
    assert node['parameters']['filters']['foldersToInclude']==['SELECT_SHRUBBERY_FOLDER']
    assert not workflow['active']
