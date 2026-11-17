"""Validate web addresses before handing them to either browser."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit, parse_qsl

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


# These are verification policies, not URL rewrites. Navigation always uses the original URL.
TRACKING_PARAMETERS = frozenset({
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
    'gclid', 'dclid', 'fbclid', 'msclkid',
})


@dataclass(frozen=True)
class DestinationPolicy:
    """Trusted backend configuration only; never supplied by the model or a page."""
    identity_parameters: frozenset[str] = frozenset()
    removable_parameters: frozenset[str] = frozenset()
    allow_trailing_slash: bool = False


DESTINATION_POLICIES = {
    (host, '/watch'): DestinationPolicy(
        identity_parameters=frozenset({'v'}), removable_parameters=frozenset({'pp'}))
    for host in ('youtube.com', 'www.youtube.com', 'm.youtube.com')
}
_UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


def _canonical_component(value):
    if re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise ValueError('Invalid percent encoding.')
    def replace(match):
        character = chr(int(match.group()[1:], 16))
        return character if character in _UNRESERVED else match.group().upper()
    return re.sub(r'%[0-9a-fA-F]{2}', replace, value)


def _destination_parts(value):
    # The launch validator accepts bare domains; observed destinations must be full web URLs.
    if not isinstance(value, str) or urlsplit(value).scheme.lower() not in {'http', 'https'}:
        raise ValueError('Expected a full HTTP/HTTPS destination.')
    parts = urlsplit(normalize_web_url(value))
    path = _canonical_component(parts.path or '/')
    fragment = _canonical_component(parts.fragment)
    _canonical_component(parts.query)  # Reject malformed escapes before query decoding.
    query = {}
    for key, item in parse_qsl(parts.query, keep_blank_values=True, errors='strict', max_num_fields=256):
        query.setdefault(key, []).append(item)
    return parts, path, fragment, query


def destination_matches(expected: str, actual: str, *, policy: DestinationPolicy | None = None) -> bool:
    """Conservative resource identity, permitting only explicitly removable URL decoration.

    Unknown query keys/values and duplicate-value order remain significant. Fragments,
    non-default ports, schemes and hosts remain significant. Only root slash normalization
    is universal; other slash aliases need a trusted path policy. No network or model calls.
    """
    try:
        e, epath, efragment, eq = _destination_parts(expected)
        a, apath, afragment, aq = _destination_parts(actual)
        if (e.scheme, e.hostname, e.port) != (a.scheme, a.hostname, a.port):
            return False
        route_policy = DESTINATION_POLICIES.get((e.hostname, epath), DestinationPolicy())
        custom = policy or DestinationPolicy()
        required = route_policy.identity_parameters | custom.identity_parameters
        ignored = (TRACKING_PARAMETERS | route_policy.removable_parameters | custom.removable_parameters) - required
        if route_policy.allow_trailing_slash or custom.allow_trailing_slash:
            epath = epath[:-1] if epath.endswith('/') and epath != '/' else epath
            apath = apath[:-1] if apath.endswith('/') and apath != '/' else apath
        if epath != apath or efragment != afragment:
            return False
        for key in required:
            if len(eq.get(key, [])) != 1 or not eq[key][0] or eq.get(key) != aq.get(key):
                return False
        return ({key: values for key, values in eq.items() if key not in ignored}
                == {key: values for key, values in aq.items() if key not in ignored})
    except (ValueError, TypeError, UnicodeError):
        return False
