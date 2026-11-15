from unittest.mock import MagicMock
import mailbox_check


def test_mail_diagnostic_is_readonly_and_reports_actual_arrival(capsys):
    client = MagicMock()
    client.select.return_value = ('OK', [b'1'])
    client.uid.side_effect = [('OK', [b'123']), ('OK', [(b'1 (UID 123 INTERNALDATE "14-Nov-2026 00:42:00 +0000")',
        b'Subject: Latest school email\r\nTo: automation@icloud.com\r\nDate: Fri, 13 Nov 2026 12:00:00 +0000\r\n\r\n'), b')'])]
    mailbox_check.check({'mail': {'username': 'primary@icloud.com', 'password': 'secret-value', 'alias': 'automation@icloud.com'}}, lambda *a, **kw: client)
    client.select.assert_called_once_with('INBOX', readonly=True)
    assert 'BODY.PEEK' in client.uid.call_args_list[1].args[2]
    output = capsys.readouterr().out
    assert '14-Nov-2026 00:42:00' in output and 'Latest school email' in output
    assert 'secret-value' not in output
    client.store.assert_not_called()
    client.logout.assert_called_once()


def test_mail_diagnostic_sorts_unsorted_uids_numerically_before_taking_five(capsys):
    client = MagicMock()
    client.select.return_value = ('OK', [b'11'])
    # Newest UID appears first; the tail contains the exact older UIDs seen live.
    def uid(command, *args):
        if command == 'search':
            return 'OK', [b'1000 999 9 8 7 6 281 284 285 286 287']
        identity = args[0]
        return 'OK', [(b'1 (UID ' + identity + b' INTERNALDATE "15-Nov-2026 02:12:00 +0000")',
                       b'Subject: Email UID ' + identity + b'\r\nTo: automation@icloud.com\r\n\r\n'), b')']
    client.uid.side_effect = uid
    mailbox_check.check({'mail': {'username': 'primary@icloud.com', 'password': 'secret', 'alias': 'automation@icloud.com'}}, lambda *a, **kw: client)
    fetched = [call.args[1] for call in client.uid.call_args_list if call.args[0] == 'fetch']
    assert fetched == [b'1000', b'999', b'287', b'286', b'285']
    output = capsys.readouterr().out
    assert 'Matching messages: 11' in output
    assert 'UID: 1000' in output
    assert 'UID: 281' not in output


def test_missing_fetch_headers_are_reported_instead_of_silently_omitted(capsys):
    client = MagicMock()
    client.select.return_value = ('OK', [b'1'])
    client.uid.side_effect = [('OK', [b'1000']), ('OK', [None])]
    mailbox_check.check({'mail': {'username': 'primary@icloud.com', 'password': 'secret', 'alias': 'automation@icloud.com'}}, lambda *a, **kw: client)
    assert 'UID 1000: headers unavailable' in capsys.readouterr().out
