"""Redact credentials at console and persisted diagnostic boundaries."""
import builtins
import os
import re
from urllib.parse import quote

_secrets = set()


def register_secret(value):
    if value:
        _secrets.add(str(value))
    return value


def redact(value):
    text = str(value)
    secrets = _secrets | {os.environ.get(name) for name in
                         ('ODDS_API_KEY', 'GEMINI_API_KEY', 'FP_PRO_API_KEY', 'GITHUB_TOKEN')}
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        if secret:
            for variant in (secret, quote(secret, safe='')):
                text = text.replace(variant, '[REDACTED]')
    return re.sub(r'(?i)((?:api[_-]?key|access_token|token)\s*[=:]\s*)[^\s&\'"<>]+',
                  r'\1[REDACTED]', text)


def safe_print(*args, **kwargs):
    builtins.print(*(redact(arg) for arg in args), **kwargs)
