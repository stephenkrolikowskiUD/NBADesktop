"""Small, offline-testable guards around paid pregame market requests."""
from datetime import datetime, timezone
import requests
from nba_security import redact


def is_pregame(event, now=None):
    try:
        start = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
        return start.tzinfo is not None and start > (now or datetime.now(timezone.utc))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def fetch_batch(url, params, quota_check, warn):
    """Return one validated batch or warn and let other game/batch pulls continue."""
    try:
        response = requests.get(url, params=params, timeout=15)
        quota_check(response, 'NBA event props')
        if response.status_code != 200:
            raise ValueError(f'Props HTTP {response.status_code}')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('bookmakers'), list):
            raise ValueError('Malformed props response')
        for book in data['bookmakers']:
            if not isinstance(book, dict) or not isinstance(book.get('markets'), list):
                raise ValueError('Malformed bookmaker response')
            for market in book['markets']:
                if not isinstance(market, dict) or not isinstance(market.get('outcomes'), list):
                    raise ValueError('Malformed market response')
                if not all(isinstance(row, dict) for row in market['outcomes']):
                    raise ValueError('Malformed market outcome')
        return data
    except (requests.RequestException, ValueError, TypeError) as error:
        warn(redact(f'Incomplete props coverage: {type(error).__name__}: {error}'))
        return None
