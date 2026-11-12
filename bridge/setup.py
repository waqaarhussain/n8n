"""Interactive server-only setup. Never send credentials to GitHub or chat."""
import argparse
import getpass
import json
import os
from pathlib import Path
import caldav
from app import CONFIG, connect_calendar


def ask(label, old=''):
    value = input(f'{label}' + (f' [{old}]' if old else '') + ': ').strip()
    return value or old


def secret(label, old=''):
    return getpass.getpass(label + (' [Enter keeps saved value]' if old else '') + ': ').strip() or old


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
    args = parser.parse_args()
    settings = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if args.check:
        client, calendar = connect_calendar(settings)
        try:
            print('Connected to iCloud. Selected calendar: ' + str(calendar.name))
            print('Read-only check passed. No test events created.')
        finally:
            client.close()
        return
    print('\niCloud setup. Create an APP-SPECIFIC password at https://account.apple.com/')
    print('Your normal Apple password will not work. Entries are saved only on this server.')
    old = settings.get('icloud', {})
    while True:
        username = ask('Apple Account email', old.get('username', ''))
        password = secret('Apple app-specific password', old.get('password', ''))
        client = None
        try:
            client = caldav.DAVClient(url='https://caldav.icloud.com/', username=username,
                                     password=password, timeout=30)
            calendars = client.principal().calendars()
            if not calendars:
                raise ValueError('No calendars available')
            print('\nAvailable iCloud calendars:')
            for i, calendar in enumerate(calendars, 1):
                print(f'  {i}) {calendar.name}')
            while True:
                choice = ask('Calendar number')
                if choice.isdigit() and 1 <= int(choice) <= len(calendars):
                    selected = calendars[int(choice) - 1]
                    break
                print('Select one of the listed numbers.')
            settings['icloud'] = {'username': username, 'password': password,
                                  'calendar_url': str(selected.url), 'calendar_name': str(selected.name)}
            break
        except Exception as exc:
            print(f'iCloud check failed ({type(exc).__name__}). Check account, app password and connectivity.')
            if ask('Try again? y/n', 'y').lower() != 'y':
                raise SystemExit('Setup stopped. Existing settings have not been changed.')
        finally:
            if client:
                client.close()
    ai = settings.get('ai', {})
    print('\nEvent extraction uses the OpenAI API, billed separately from ChatGPT Plus.')
    key = secret('OpenAI API key (blank leaves extraction unconfigured)', ai.get('api_key', ''))
    model = ask('OpenAI model', ai.get('model', 'gpt-4.1-mini'))
    settings['ai'] = {'api_key': key, 'model': model}
    settings['default_duration_minutes'] = 60
    settings.setdefault('allowed_senders', [])
    print('\nInOut WhatsApp. Each recipient generates their own key by messaging Create APIKey to the bot.')
    recipients = settings.get('inout', {}).get('recipients', [])
    if recipients and ask('Replace saved recipient list? y/n', 'n').lower() == 'y':
        recipients = []
    while ask('Add a WhatsApp recipient? y/n', 'y' if not recipients else 'n').lower() == 'y':
        label = ask('Recipient label (e.g. Me)')
        recipient_key = secret('Their InOut API key')
        if recipient_key:
            recipients.append({'label': label, 'api_key': recipient_key})
    settings['inout'] = {'recipients': recipients, 'chunk_size': 350}
    save(settings)
    print('\nSaved. iCloud discovery passed; calendar write and WhatsApp delivery still need a live test.')
    if not key:
        print('AI extraction is NOT configured. Run n8n-setup again after obtaining an API key.')


if __name__ == '__main__':
    main()
