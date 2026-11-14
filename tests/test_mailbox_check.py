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
