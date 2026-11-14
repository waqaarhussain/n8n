"""Read-only iCloud IMAP arrival diagnostic. Never changes read flags or prints passwords."""
from email import message_from_bytes
from email.header import decode_header, make_header
import imaplib
import re
from app import config


def decoded(value):
    return str(make_header(decode_header(value or '')))


def check(settings, connection_factory=imaplib.IMAP4_SSL):
    mail = settings.get('mail', {})
    if not all(mail.get(field) for field in ('username', 'password', 'alias')):
        raise ValueError('iCloud Mail settings incomplete. Run n8n-setup.')
    client = connection_factory('imap.mail.me.com', 993, timeout=30)
    try:
        client.login(mail['username'], mail['password'])
        status, _ = client.select('INBOX', readonly=True)
        if status != 'OK':
            raise ValueError('Could not open INBOX read-only.')
        alias = mail['alias'].replace('\\', '\\\\').replace('"', '\\"')
        status, rows = client.uid('search', None, 'TO', '"' + alias + '"')
        if status != 'OK':
            raise ValueError('IMAP alias search failed.')
        identifiers = rows[0].split() if rows and rows[0] else []
        print('Connected to INBOX read-only. Alias: ' + mail['alias'])
        print(f'Matching messages: {len(identifiers)}. Showing the newest five by mailbox UID.')
        for identity in identifiers[-5:]:
            status, fetched = client.uid('fetch', identity, '(INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT TO DATE)])')
            if status != 'OK':
                print('UID ' + identity.decode() + ': header fetch failed')
                continue
            for part in fetched:
                if not isinstance(part, tuple):
                    continue
                metadata, raw_headers = part
                internal = re.search(rb'INTERNALDATE "([^"]+)"', metadata)
                message = message_from_bytes(raw_headers)
                print('\nUID: ' + identity.decode())
                print('Arrived at mailbox: ' + (internal[1].decode() if internal else 'unknown'))
                print('Subject: ' + decoded(message.get('Subject')))
                print('To: ' + decoded(message.get('To')))
        print('\nNo messages changed. Calendar processing and duplicate history are untouched.')
    finally:
        try:
            client.logout()
        except Exception:
            pass


def main():
    try:
        check(config())
    except Exception as exc:
        raise SystemExit('Read-only mail check failed (' + type(exc).__name__ + '). Check Mail credentials/network access; passwords are not printed.') from None


if __name__ == '__main__':
    main()
