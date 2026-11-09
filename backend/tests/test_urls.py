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
