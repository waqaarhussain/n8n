"""Optional TextMeBot notifications, isolated from calendar processing."""
import hashlib
import html
import re

import httpx


class DeliveryError(ValueError):
    pass


def send(settings, text):
    key = settings.get('api_key', '')
    recipient = settings.get('recipient', '')
    if not key or not recipient:
        raise DeliveryError('Configure TextMeBot first: n8n-live setup-textmebot')
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get('https://api.textmebot.com/send.php', params={
                'recipient': recipient, 'apikey': key, 'text': text})
    except httpx.TimeoutException:
        raise DeliveryError('TextMeBot timed out. Check WhatsApp before retrying.') from None
    except httpx.RequestError:
        raise DeliveryError('Could not connect to TextMeBot; check server network/DNS.') from None
    # Never print a request URL, since this provider requires the key in its query.
    detail = response.text.replace(key, '[REDACTED]')
    detail = re.sub(r'https?://\S+', '[URL REDACTED]', detail)
    detail = re.sub(r'(?i)(apikey\s*[=:]\s*)[^\s&"<>]+', r'\1[REDACTED]', detail)
    detail = re.sub('<[^>]*>', ' ', detail)
    detail = re.sub(r'\s+', ' ', html.unescape(detail)).strip()[:400]
    if response.status_code >= 300:
        raise DeliveryError(f'TextMeBot HTTP {response.status_code}: {detail}')
    try:
        value = response.json()
    except ValueError:
        value = None
    if ((isinstance(value, dict) and (value.get('error') or value.get('success') is False
            or str(value.get('status', '')).lower() in ('error', 'failed')))
            or re.match(r'(?i)^(error\b|failed\b)', detail)):
        raise DeliveryError('TextMeBot rejected the request: ' + detail)
    return f'TextMeBot HTTP {response.status_code}: ' + (detail or '(empty response)')


def clean_body(body):
    kept = []
    for line in body.splitlines():
        if re.match(r'^\s*(The Shrubbery School Limited\b|The information contained in this (e-?mail|message)\b|This (e-?mail|message)( and (any |its )?attachments)? is confidential\b)', line, re.I):
            break
        if not re.fullmatch(r'\s*[_=\-]{5,}\s*', line):
            kept.append(line.rstrip())
    while kept and (not kept[-1].strip() or re.fullmatch(r'\s*(many thanks|thank you|kind regards|best regards|regards)[.,!]?\s*', kept[-1], re.I)):
        kept.pop()
    return '\n'.join(kept).strip()


def parts(text, size=3000):
    result = []
    while len(text) > size:
        cut = text.rfind('\n', 0, size + 1)
        if cut < size // 3:
            cut = text.rfind(' ', 0, size + 1)
        if cut < size // 3:
            cut = size
        for url in re.finditer(r'https?://[^\s]+', text):
            if url.start() < cut < url.end():
                if url.start() == 0:
                    raise DeliveryError('A link exceeds the message chunk limit; review manually.')
                cut = url.start()
                break
        result.append(text[:cut])
        text = text[cut:]
    if text:
        result.append(text)
    return result


def notify(db, settings, mail_id, title, body):
    if not settings.get('enabled'):
        return {'status': 'disabled'}
    db.execute('CREATE TABLE IF NOT EXISTS textmebot_attempts (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
    recipient = hashlib.sha256((settings.get('api_key', '') + ':' + settings.get('recipient', '')).encode()).hexdigest()[:20]
    accepted = 0
    for index, piece in enumerate(parts('Email From School\n\nSubject: ' + title + '\n\n' + clean_body(body))):
        identity = f'{mail_id}:{recipient}:{index}'
        row = db.execute('SELECT status FROM textmebot_attempts WHERE id=?', (identity,)).fetchone()
        if row:
            if row['status'] != 'request_returned':
                return {'status': 'held', 'reason': 'Earlier attempt is uncertain. Check WhatsApp before retrying.'}
            continue
        db.execute('INSERT INTO textmebot_attempts VALUES (?, ?)', (identity, 'sending'))
        db.commit()
        try:
            send(settings, piece)
        except DeliveryError as exc:
            db.execute('UPDATE textmebot_attempts SET status=? WHERE id=?', ('uncertain', identity))
            db.commit()
            return {'status': 'uncertain', 'reason': str(exc)}
        db.execute('UPDATE textmebot_attempts SET status=? WHERE id=?', ('request_returned', identity))
        db.commit()
        accepted += 1
    return {'status': 'request_returned' if accepted else 'duplicate', 'requests': accepted,
            'note': 'An HTTP response is not a WhatsApp delivery receipt.'}


def main():
    from app import config
    settings = config().get('textmebot', {})
    try:
        print(send(settings, 'Email From School\n\nTextMeBot connection test.'))
        print('Check WhatsApp for the actual message. Automatic notifications remain as configured.')
    except DeliveryError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
