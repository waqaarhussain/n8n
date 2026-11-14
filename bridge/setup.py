"""Interactive server-only setup. Never send credentials to GitHub or chat."""
import argparse
import getpass
import imaplib
import re
import json
import os
import sys
import termios
from pathlib import Path
import caldav
from app import CONFIG, connect_calendar


def ask(label, old=''):
    value = input(f'{label}' + (f' [{old}]' if old else '') + ': ').strip()
    return value or old


def mask_key(value):
    if not value:
        return '(not entered)'
    if len(value) >= 16:
        return value[:4] + '…' + value[-4:] + f' ({len(value)} characters)'
    if len(value) >= 8:
        return value[:2] + '…' + value[-2:] + f' ({len(value)} characters)'
    return '•' * len(value) + f' ({len(value)} characters)'


MODEL = 'gpt-6.1-sol'
MAIL_USER = 'hackdroid@icloud.com'
MAIL_ALIAS = 'waqaarn8n@icloud.com'


def preview_input(prompt):
    """TTY input with a masked preview; never echo the complete API key."""
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    hidden = termios.tcgetattr(fd)
    hidden[3] &= ~(termios.ECHO | termios.ICANON)
    hidden[6][termios.VMIN] = 1
    hidden[6][termios.VTIME] = 0
    value = ''
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, hidden)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            char = os.read(fd, 1).decode('ascii', errors='ignore')
            if char in ('\r', '\n'):
                return value
            if char == '\x04':
                raise EOFError
            if char in ('\x7f', '\b'):
                value = value[:-1]
            elif char == '\x15':
                value = ''
            elif char and char.isprintable() and not char.isspace():
                value += char
            sys.stdout.write('\r\x1b[2K' + prompt + mask_key(value))
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        print()


def secret(label, old='', preview=False):
    reader = preview_input if preview else getpass.getpass
    value = reader(label + (' [Enter keeps saved value]' if old else '') + ': ').strip() or old
    if preview:
        print('Key received: ' + mask_key(value))
    return value


def save(settings):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')
    os.chown(temporary, 0, 1000)
    os.chmod(temporary, 0o640)
    temporary.replace(CONFIG)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--select-calendar', action='store_true')
    parser.add_argument('--remove-whatsapp', action='store_true')
    parser.add_argument('--remove-inout', action='store_true')
    parser.add_argument('--textmebot', action='store_true')
    parser.add_argument('--enable-textmebot', action='store_true')
    args = parser.parse_args()
    settings = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if args.textmebot:
        old = settings.get('textmebot', {})
        print('Link your WhatsApp number using the TextMeBot email link first.')
        key = secret('TextMeBot API key', old.get('api_key', ''), preview=True)
        recipient = ask('Recipient WhatsApp number with country code (e.g. +447...)', old.get('recipient', ''))
        recipient = re.sub(r'[ ()-]', '', recipient)
        if not key or not re.fullmatch(r'\+?[1-9]\d{6,14}', recipient):
            raise SystemExit('A key and a valid international phone number are required. Nothing saved.')
        settings['textmebot'] = {'api_key': key, 'recipient': recipient, 'enabled': False}
        settings.pop('inout', None)
        save(settings)
        print('Saved. Automatic notifications are OFF. Run n8n-live check-textmebot.')
        return
    if args.enable_textmebot:
        if not all(settings.get('textmebot', {}).get(k) for k in ('api_key', 'recipient')):
            raise SystemExit('Run n8n-live setup-textmebot first.')
        settings['textmebot']['enabled'] = True
        save(settings)
        print('TextMeBot email notifications enabled. Calendar settings preserved.')
        return
    if args.remove_whatsapp or args.remove_inout:
        settings.pop('inout', None)
        if args.remove_whatsapp:
            settings.pop('textmebot', None)
        save(settings)
        print('Legacy InOut settings removed.' if args.remove_inout else 'Messaging disabled. Calendar settings preserved.')
        return
    if args.check:
        client, calendar = connect_calendar(settings)
        try:
            print('Connected to iCloud. Selected calendar: ' + str(calendar.name))
            print('Read-only check passed. No test events created.')
        finally:
            client.close()
        return
    if args.select_calendar:
        old = settings.get('icloud', {})
        if not all(old.get(k) for k in ('username', 'password')):
            raise SystemExit('Run n8n-setup first to save Apple credentials.')
        client = caldav.DAVClient(url='https://caldav.icloud.com/', username=old['username'],
                                 password=old['password'], timeout=30)
        try:
            calendars = client.principal().calendars()
            if not calendars:
                raise SystemExit('No iCloud calendars available. Nothing imported.')
            print('Select the calendar for the email workflow:')
            for i, calendar in enumerate(calendars, 1):
                print(f'  {i}) {calendar.name}')
            while True:
                choice = ask('Calendar number')
                if choice.isdigit() and 1 <= int(choice) <= len(calendars):
                    selected = calendars[int(choice) - 1]
                    break
                print('Select one of the listed numbers.')
            old.update(calendar_url=str(selected.url), calendar_name=str(selected.name))
            settings['icloud'] = old
            save(settings)
            print('Saved calendar: ' + str(selected.name))
        finally:
            client.close()
        return
    print('\niCloud setup. Create an APP-SPECIFIC password at https://account.apple.com/')
    print('Your normal Apple password will not work. Entries are saved only on this server.')
    old = settings.get('icloud', {})
    while True:
        username = ask('Apple Account email', old.get('username', 'waqaar06@hotmail.com'))
        password = secret('Apple app-specific password', old.get('password', ''))
        client = None
        try:
            client = caldav.DAVClient(url='https://caldav.icloud.com/', username=username,
                                     password=password, timeout=30)
            client.principal()
            settings['icloud'] = dict(old, username=username, password=password)
            if old.get('username') != username:
                settings['icloud'].pop('calendar_url', None)
                settings['icloud'].pop('calendar_name', None)
            break
        except Exception as exc:
            print(f'iCloud check failed ({type(exc).__name__}). Check account, app password and connectivity.')
            if ask('Try again? y/n', 'y').lower() != 'y':
                raise SystemExit('Setup stopped. Existing settings have not been changed.')
        finally:
            if client:
                client.close()
    print('\niCloud Mail receives forwarded emails. Your alias shares the primary mailbox.')
    print('Use the PRIMARY iCloud Mail address for login, not the automation alias.')
    old_mail = settings.get('mail', {})
    while True:
        mail_user, alias = MAIL_USER, MAIL_ALIAS
        print(f'Mail login: {mail_user}; forwarding alias: {alias}')
        mail_password = secret('iCloud Mail app-specific password', old_mail.get('password', password))
        connection = None
        try:
            connection = imaplib.IMAP4_SSL('imap.mail.me.com', 993, timeout=30)
            connection.login(mail_user, mail_password)
            status, _ = connection.select('INBOX', readonly=True)
            if status != 'OK':
                raise ValueError('Cannot open INBOX')
            settings['mail'] = {'mode': 'icloud_alias', 'username': mail_user,
                                'password': mail_password, 'alias': alias.lower()}
            print('IMAP login and read-only INBOX check passed. No messages changed.')
            break
        except Exception as exc:
            print(f'iCloud Mail check failed ({type(exc).__name__}). Check primary address and app-specific password.')
            if ask('Try again? y/n', 'y').lower() != 'y':
                raise SystemExit('Setup stopped. Existing settings have not been changed.')
        finally:
            if connection:
                try:
                    connection.logout()
                except Exception:
                    pass
    ai = settings.get('ai', {})
    print('\nEvent extraction uses the OpenAI API, billed separately from ChatGPT Plus.')
    key = secret('OpenAI API key (blank leaves extraction unconfigured)', ai.get('api_key', ''), preview=True)
    model = MODEL
    print('OpenAI model: ' + model)
    settings['ai'] = {'api_key': key, 'model': model}
    settings['default_duration_minutes'] = 60
    settings.setdefault('allowed_senders', [])
    settings.pop('inout', None)
    save(settings)
    print('\nSaved shared credentials. Choose a calendar when running n8n-live import.')
    if not key:
        print('AI extraction is NOT configured. Run n8n-setup again after obtaining an API key.')


if __name__ == '__main__':
    main()
