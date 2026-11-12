"""Send one short diagnostic per configured recipient. Never print API keys."""
from app import config, InOutError, send_inout


def main():
    recipients = config().get('inout', {}).get('recipients', [])
    if not recipients:
        raise SystemExit('No InOut recipients configured. Run n8n-setup.')
    for recipient in recipients:
        label = recipient.get('label') or 'Unnamed recipient'
        key = recipient.get('api_key', '')
        print('Checking recipient: ' + label, flush=True)
        if not key:
            print('Missing InOut API key.')
            continue
        try:
            send_inout(key, 'Email From School\n\nn8n WhatsApp connection test.')
            print('Provider request accepted. Check the recipient WhatsApp chat; this is not a delivery receipt.')
        except InOutError as exc:
            print(str(exc))
        except Exception as exc:
            print('Unexpected error: ' + type(exc).__name__)


if __name__ == '__main__':
    main()
