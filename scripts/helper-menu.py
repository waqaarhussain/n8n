"""Numbered menu for this server's n8n maintenance helpers."""
import subprocess

OPTIONS = [
    ('stats', 'Pause or resume overview stats for a workflow'),
    ('stats-reset', 'Reset overview stats (keeps execution logs and calendar events)'),
    ('review-last', 'Show recent email event proposals and supporting quotes'),
    ('status', 'Show service status'),
    ('logs', 'Show recent n8n and calendar bridge logs'),
    ('check-icloud', 'Check iCloud calendar connection without creating events'),
    ('check-mail', 'Check iCloud inbox and recent alias arrivals read-only'),
    ('setup', 'Change shared Apple credentials or OpenAI key'),
    ('update', 'Back up and update this n8n project'),
    ('backup', 'Create a private backup'),
    ('school-import', 'Choose calendar and import email workflow (replaces its node settings)'),
    ('web', 'Change the web UI IP address or hostname'),
    ('upgrade-engine', 'Back up and explicitly upgrade the n8n engine'),
    ('remove-whatsapp', 'Remove all saved WhatsApp provider settings'),
    ('setup-textmebot', 'Optional TextMeBot setup (automatic messages stay off)'),
    ('check-textmebot', 'Send one TextMeBot test message'),
    ('enable-textmebot', 'Enable configured TextMeBot email notifications'),
]


def choose():
    print('\nn8n helpers\n')
    for number, (_, label) in enumerate(OPTIONS, 1):
        print(f'{number}) {label}')
    print('0) Exit')
    while True:
        value = input('\nChoose helper number: ').strip()
        if value in ('', '0'):
            return None
        if value.isdigit() and 1 <= int(value) <= len(OPTIONS):
            return OPTIONS[int(value) - 1][0]
        print('Choose a listed number.')


def main():
    choice = choose()
    if choice:
        raise SystemExit(subprocess.call(['/usr/local/bin/n8n-live', choice]))


if __name__ == '__main__':
    main()
