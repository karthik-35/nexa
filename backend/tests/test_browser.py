"""Real Chromium tests using local HTML; no OpenAI calls or user profile."""
import time
import pytest
from playwright.sync_api import sync_playwright
from app import browser_tools as tools
from app.ai import png_dimensions

HTML = '''<!doctype html><html><body style="margin:0; height:2200px; background:white">
<button aria-label="Search with your voice" onclick="document.querySelector('dialog').showModal()"
 style="position:absolute;left:250px;top:20px;width:40px;height:40px"><svg width="10" height="10"><circle cx="5" cy="5" r="4"/></svg></button>
<button style="position:absolute;left:20px;top:20px" onclick="this.textContent='Filter options'">Filters</button>
<input placeholder="Search" style="position:absolute;left:20px;top:70px">
<canvas width="80" height="60" style="position:absolute;left:20px;top:120px;background:blue"
 onclick="document.querySelector('dialog').showModal()"></canvas>
<dialog>Voice search ready<button onclick="this.closest('dialog').close()">Close</button></dialog>
</body></html>'''


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, monkeypatch):
    context = browser.new_context(viewport={'width': 800, 'height': 600}, device_scale_factor=2)
    page = context.new_page()
    page.set_default_timeout(250)
    page.set_content(HTML)
    monkeypatch.setattr(tools, 'get_page', lambda: page)
    yield page
    tools.clear_observation()
    context.close()


def vision(monkeypatch, elements=None):
    monkeypatch.setattr(tools, 'analyze_screenshot', lambda path, **kwargs: {
        'page': 'Local test', 'summary': 'Controls', 'elements': elements or [
            {'description': 'blue canvas control', 'visible_text': None, 'element_id': None, 'x': 60, 'y': 150},
        ],
    })


def test_retina_screenshot_uses_css_coordinates(page):
    assert png_dimensions(page.screenshot()) == (1600, 1200)
    shot = tools.take_screenshot()
    assert shot['dimensions']['image'] == (800, 600)
    assert shot['dimensions']['viewport']['devicePixelRatio'] == 2


def test_icon_discovered_by_accessibility_label_and_dom_click_opens_dialog(page):
    observation = tools.get_page_elements()
    voice = next(e for e in observation['elements'] if e['name'] == 'Search with your voice')
    assert voice['role'] == 'button' and not voice['text']
    result = tools.click_element(observation['observation_id'], voice['id'])
    assert result['success'] and result['method'] == 'dom'
    assert page.locator('dialog').is_visible()
    assert 'Voice search ready' in result['verification']['new_dialogs'][0]
    assert not tools.click_element(observation['observation_id'], voice['id'])['success']


def test_visual_fallback_actually_activates_canvas_at_dpr_two(page, monkeypatch):
    vision(monkeypatch)
    observation = tools.analyze_current_screen()
    assert observation['dimensions']['width'] == 800
    result = tools.click_coordinates(60, 150, observation['observation_id'])
    assert result['success'] and result['method'] == 'coordinates'
    assert page.locator('dialog').is_visible()
    assert result['verification']['new_dialogs']


def test_unobserved_or_changed_coordinates_never_click(page, monkeypatch):
    assert not tools.click_coordinates(60, 150, 'invented')['success']
    vision(monkeypatch)
    observation = tools.analyze_current_screen()
    assert not tools.click_coordinates(61, 150, observation['observation_id'])['success']
    assert not tools.click_coordinates(True, 150, observation['observation_id'])['success']
    assert not page.locator('dialog').is_visible()


@pytest.mark.parametrize('change', ['scroll', 'resize', 'image', 'expired', 'new_observation', 'navigate'])
def test_stale_coordinates_fail_closed(page, monkeypatch, change):
    vision(monkeypatch)
    observation = tools.analyze_current_screen()
    if change == 'scroll':
        page.evaluate('window.scrollTo(0,100)')
    elif change == 'resize':
        page.set_viewport_size({'width': 900, 'height': 600})
    elif change == 'image':
        page.locator('canvas').evaluate("el => el.style.background='red'")
    elif change == 'expired':
        tools._observation.created = time.monotonic() - 121
    elif change == 'new_observation':
        tools.get_page_elements()
    else:
        page.goto('about:blank#different')
    result = tools.click_coordinates(60, 150, observation['observation_id'])
    assert not result['success']
    assert not page.locator('dialog').is_visible()


@pytest.mark.parametrize('element', [
    {'description': 'bad', 'visible_text': None, 'element_id': None, 'x': 801, 'y': 150},
    {'description': 'bad', 'visible_text': None, 'element_id': 'invented', 'x': None, 'y': None},
    {'description': 'bad', 'visible_text': None, 'element_id': None, 'x': 60, 'y': None},
])
def test_bad_vision_output_does_not_authorize_clicks(page, monkeypatch, element):
    vision(monkeypatch, [element])
    with pytest.raises(ValueError):
        tools.analyze_current_screen()
    assert tools._observation is None


def test_changed_dom_identity_is_rejected(page):
    observation = tools.get_page_elements()
    voice = next(e for e in observation['elements'] if e['name'] == 'Search with your voice')
    page.get_by_role('button', name='Search with your voice').evaluate("el => el.ariaLabel='Buy now'")
    assert not tools.click_element(observation['observation_id'], voice['id'])['success']
    assert not page.locator('dialog').is_visible()


def test_click_without_state_change_is_explicitly_unverified(page):
    page.get_by_role('button', name='Search with your voice').evaluate("el => el.removeAttribute('onclick')")
    observation = tools.get_page_elements()
    voice = next(e for e in observation['elements'] if e['name'] == 'Search with your voice')
    result = tools.click_element(observation['observation_id'], voice['id'])
    assert result['action_executed'] and result['verification']['status'] == 'unverified'
    assert not page.locator('dialog').is_visible()


def test_existing_read_click_type_enter_scroll_controls(page):
    assert 'Filters' in tools.read_page()['content']
    assert tools.click_text('Filters')['success']
    assert page.get_by_role('button', name='Filter options').is_visible()
    assert tools.type_text('Search', 'AI agents')['success']
    assert page.get_by_placeholder('Search').input_value() == 'AI agents'
    page.get_by_placeholder('Search').evaluate("el => el.onkeydown = e => { if(e.key === 'Enter') el.dataset.enter = 'yes'; }")
    assert tools.press_enter()['success']
    assert page.get_by_placeholder('Search').get_attribute('data-enter') == 'yes'
    assert tools.scroll_page('down')['success']
    page.wait_for_function('scrollY > 2')
    assert tools.stop_scrolling()['success']
    assert page.evaluate('window.__nexaScrolling') is False
    assert not tools.scroll_page('diagonal')['success']
    assert not tools.click_text('nonexistent label')['success']


def test_vision_prefers_dom_id_and_forbids_coordinate_bypass(page, monkeypatch):
    def analyze(path, *, dom_elements):
        voice = next(e for e in dom_elements if e['name'] == 'Search with your voice')
        return {'page': 'Test', 'summary': 'Voice button', 'elements': [
            {'description': 'microphone icon', 'visible_text': None,
             'element_id': voice['id'], 'x': 270, 'y': 40},
        ]}
    monkeypatch.setattr(tools, 'analyze_screenshot', analyze)
    observation = tools.analyze_current_screen()
    assert not tools.click_coordinates(270, 40, observation['observation_id'])['success']
    element_id = observation['analysis']['elements'][0]['element_id']
    result = tools.click_element(observation['observation_id'], element_id)
    assert result['success'] and page.locator('dialog').is_visible()


def test_verification_failure_does_not_erase_dispatched_click(page, monkeypatch):
    from app import browser_observation
    from playwright.sync_api import Error
    observation = tools.get_page_elements()
    voice = next(e for e in observation['elements'] if e['name'] == 'Search with your voice')
    def unavailable(page):
        raise Error('Page observation failed after click')
    monkeypatch.setattr(browser_observation, 'visible_state', unavailable)
    result = tools.click_element(observation['observation_id'], voice['id'])
    assert result['action_executed']
    assert result['verification']['status'] == 'unverified'
    assert page.locator('dialog').is_visible()


def test_visible_blocker_does_not_allow_click_through(page):
    observation = tools.get_page_elements()
    voice = next(e for e in observation['elements'] if e['name'] == 'Search with your voice')
    page.evaluate("""() => {
        const overlay = document.createElement('div');
        overlay.style = 'position:fixed;inset:0;z-index:9999;background:white';
        document.body.append(overlay);
    }""")
    result = tools.click_element(observation['observation_id'], voice['id'])
    assert not result['success'] and result['error'] == 'browser_timeout'
    assert not page.locator('dialog').is_visible()


def test_open_url_reuses_page_and_invalidates_targets(page, monkeypatch):
    from unittest.mock import Mock
    from app import mac_tools
    launch = Mock()
    monkeypatch.setattr(mac_tools.subprocess, 'run', launch)
    page.route('https://www.youtube.com/**', lambda route: route.fulfill(body=HTML, content_type='text/html'))
    old_observation = tools.get_page_elements()
    old_context = page.context
    result = tools.open_browser_url('https://www.youtube.com')
    assert result['success'] and result['browser'] == 'nexa'
    assert result['url'] == 'https://www.youtube.com/'
    assert tools.get_page() is page and page.context is old_context
    assert old_context.pages == [page]
    assert tools._observation is None
    assert not tools.click_element(old_observation['observation_id'], 'e1')['success']
    assert tools.click_text('Filters')['success']
    assert 'Filter options' in tools.read_page()['content']
    launch.assert_not_called()


def test_open_then_search_youtube_keeps_same_context(page):
    home = '''<form action="/results"><input name="search_query" placeholder="Search"></form>'''
    def respond(route):
        html = home if '/results?' not in route.request.url else '<body>Python tutorials results</body>'
        route.fulfill(body=html, content_type='text/html')
    page.route('https://www.youtube.com/**', respond)
    assert tools.open_browser_url('https://www.youtube.com')['success']
    context = page.context
    assert tools.search_youtube('Python tutorials')['success']
    assert page.url == 'https://www.youtube.com/results?search_query=Python+tutorials'
    assert tools.get_page() is page and page.context is context
    assert context.pages == [page]
    assert tools.read_page()['content'] == 'Python tutorials results'


def test_open_url_handles_http_and_browser_failures(page):
    page.route('https://example.test/missing', lambda route: route.fulfill(status=404, body='Missing'))
    result = tools.open_browser_url('https://example.test/missing')
    assert not result['success'] and result['status'] == 404
    assert result['url'] == 'https://example.test/missing'
    page.route('https://example.test/fail', lambda route: route.abort())
    result = tools.open_browser_url('https://example.test/fail')
    assert not result['success'] and result['error'] == 'browser_error'
