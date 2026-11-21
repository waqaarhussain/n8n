"""Safe, shared Telegram API exception for the CLI and command worker."""

class TelegramError(Exception):
    def __init__(self, code=0, retry_after=60, method='', reason=''):
        super().__init__(f'Telegram request failed (code {code}).')
        self.code = code
        self.retry_after = max(1, min(int(retry_after), 86400))
        self.method = method
        self.reason = reason

