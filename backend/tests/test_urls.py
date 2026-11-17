from unittest.mock import Mock

import pytest

from app import browser_tools, mac_tools
from app.urls import normalize_web_url


@pytest.mark.parametrize(('value', 'expected'), [
    ('youtube.com', 'https://youtube.com/'),
    (' https://www.youtube.com ', 'https://www.youtube.com/'),
    ('HTTPS://EXAMPLE.COM/path?q=one#section', 'https://example.com/path?q=one#section'),
    ('http://localhost:8000/docs', 'http://localhost:8000/docs'),
    ('http://[::1]:8000/', 'http://[::1]:8000/'),
])
def test_normalize_web_url(value, expected):
    assert normalize_web_url(value) == expected


@pytest.mark.parametrize('url', [
    '', '   ', 'javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test',
    'ftp://example.com', 'chrome://settings', 'about:blank', '//example.com',
    '/tmp/test', 'https:///example.com', 'https:example.com', 'https://',
    'https://user:password@example.com', 'https://@example.com',
    'https://example.com:99999', 'https://example.com:bad', 'https://[invalid]/',
    'https://exa mple.com', 'https://example.com\n', 'https://example.com/\x00',
    'https://example.com\\other', '--help',
])
def test_invalid_urls_never_launch_either_browser(monkeypatch, url):
    page = Mock()
    launch = Mock()
    monkeypatch.setattr(browser_tools, 'get_page', page)
    monkeypatch.setattr(mac_tools.subprocess, 'run', launch)
    for tool in (browser_tools.open_browser_url, mac_tools.open_website):
        result = tool(url)
        assert result['success'] is False
        assert result['error'] == 'invalid_url'
        assert 'password' not in result['message']
    page.assert_not_called()
    launch.assert_not_called()


def test_explicit_default_browser_uses_macos_open(monkeypatch):
    launch = Mock()
    page = Mock()
    monkeypatch.setattr(mac_tools.subprocess, 'run', launch)
    monkeypatch.setattr(browser_tools, 'get_page', page)
    result = mac_tools.open_website('youtube.com')
    assert result['success'] and result['browser'] == 'default'
    launch.assert_called_once_with(['open', 'https://youtube.com/'], check=True)
    page.assert_not_called()


@pytest.mark.parametrize(('expected', 'actual', 'matches'), [
    ('https://example.com/page?utm_source=x&id=123', 'https://example.com/page?id=123', True),
    ('https://example.com/page?id=123', 'https://example.com/page?utm_medium=email&id=123&fbclid=x', True),
    ('https://www.youtube.com/watch?v=abc123&pp=tracking', 'https://www.youtube.com/watch?v=abc123', True),
    ('https://www.youtube.com/watch?pp=x&v=abc123', 'https://www.youtube.com/watch?v=abc123&pp=y', True),
    ('https://www.youtube.com/watch?v=abc123', 'https://www.youtube.com/watch?v=xyz999', False),
    ('https://www.youtube.com/watch?v=abc123&pp=x', 'https://www.youtube.com/watch?pp=y&v=xyz999', False),
    ('https://example.com/item?id=123', 'https://example.com/item?id=456', False),
    ('https://example.com/item?id=123', 'https://example.com/item', False),
    ('https://example.com/page', 'https://evil.example.com/page', False),
    ('https://example.com/page', 'https://www.example.com/page', False),
    ('https://example.com/page', 'http://example.com/page', False),
    ('https://example.com/page', 'https://example.com:8443/page', False),
    ('https://example.com/a?id=123', 'https://example.com/b?id=123', False),
    ('https://example.com/a?id=123&q=FastAPI+tutorial', 'https://example.com/a?q=FastAPI%20tutorial&id=123', True),
    ('HTTPS://EXAMPLE.COM:443/%7Euser?id=%31%32%33', 'https://example.com/~user?id=123', True),
    ('https://example.com', 'https://example.com/', True),
    ('https://example.com/a/', 'https://example.com/a', False),
    ('https://example.com/file.pdf/', 'https://example.com/file.pdf', False),
    ('https://example.com/a//', 'https://example.com/a/', False),
    ('https://example.com/a%2Fb', 'https://example.com/a/b', False),
    ('https://example.com/a#route/1', 'https://example.com/a#route/2', False),
    ('https://example.com/a#section', 'https://example.com/a', False),
    ('https://example.com/a?id=123&page=1', 'https://example.com/a?id=123&page=2', False),
    ('https://example.com/a?pp=x&id=123', 'https://example.com/a?id=123', False),
    ('https://www.youtube.com/results?search_query=x&pp=one', 'https://www.youtube.com/results?search_query=x', False),
    ('https://www.youtube.com/watch?v=abc&list=one&t=10&pp=x', 'https://www.youtube.com/watch?v=abc&list=two&t=10', False),
    ('https://www.youtube.com/watch?v=abc&t=10&pp=x', 'https://www.youtube.com/watch?v=abc&t=20', False),
    ('https://www.youtube.com/watch?pp=x', 'https://www.youtube.com/watch', False),
    ('https://www.youtube.com/watch?v=abc&v=xyz&pp=x', 'https://www.youtube.com/watch?v=abc', False),
    ('https://example.com/a?id=1&id=2&q=x', 'https://example.com/a?q=x&id=1&id=2', True),
    ('https://example.com/a?id=1&id=2', 'https://example.com/a?id=2&id=1', False),
    ('https://example.com/a?id=', 'https://example.com/a', False),
    ('https://example.com/a?ID=123', 'https://example.com/a?id=123', False),
    ('https://example.com/a?q=A%2BB', 'https://example.com/a?q=A+B', False),
    ('https://example.com/a?id=%GG', 'https://example.com/a?id=%GG', False),
    ('https://user:secret@example.com/a', 'https://example.com/a', False),
    ('javascript:alert(1)', 'javascript:alert(1)', False),
    ('example.com/a', 'example.com/a', False),
])
def test_destination_identity_is_conservative_and_normalization_aware(expected, actual, matches):
    from app.urls import destination_matches
    assert destination_matches(expected, actual) is matches
    assert destination_matches(actual, expected) is matches


def test_trailing_slash_alias_requires_explicit_trusted_route_policy():
    from app.urls import destination_matches, DestinationPolicy
    expected, actual = 'https://example.com/docs?id=123', 'https://example.com/docs/?id=123'
    assert not destination_matches(expected, actual)
    policy = DestinationPolicy(allow_trailing_slash=True)
    assert destination_matches(expected, actual, policy=policy)
    assert not destination_matches(expected, 'https://example.com/other/?id=123', policy=policy)
    assert not destination_matches(expected, 'https://example.com/docs/?id=456', policy=policy)


def test_explicit_identity_parameters_override_removable_parameter_policy():
    from app.urls import destination_matches, DestinationPolicy
    policy = DestinationPolicy(identity_parameters=frozenset({'id', 'utm_source'}),
                               removable_parameters=frozenset({'id', 'decoration'}))
    expected = 'https://example.com/item?id=123&utm_source=identity&decoration=x'
    assert destination_matches(expected, 'https://example.com/item?id=123&utm_source=identity', policy=policy)
    assert not destination_matches(expected, 'https://example.com/item?id=456&utm_source=identity', policy=policy)
    assert not destination_matches(expected, 'https://example.com/item?id=123&utm_source=changed', policy=policy)
