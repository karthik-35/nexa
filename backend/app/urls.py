"""Validate web addresses before handing them to either browser."""

from urllib.parse import urlsplit

from pydantic import AnyHttpUrl


def normalize_web_url(url: str) -> str:
    message = "Use an HTTP or HTTPS URL with a hostname and no embedded credentials."
    if not isinstance(url, str):
        raise ValueError(message)
    # Reject controls before trimming: URL parsers may otherwise silently remove them.
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError(message)
    value = url.strip()
    if not value or value.startswith("//") or "\\" in value or any(c.isspace() for c in value):
        raise ValueError(message)
    try:
        if not urlsplit(value).scheme:
            value = "https://" + value
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or not parts.hostname:
            raise ValueError(message)
        if (parts.username is not None or parts.password is not None or
                parts.hostname.startswith("-")):
            raise ValueError(message)
        parsed = AnyHttpUrl(value)
    except ValueError:
        # Do not echo a rejected URL: it may contain credentials.
        raise ValueError(message) from None
    return str(parsed)
