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
    assert not result['success'] and 'changed' in result['message']
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


@pytest.mark.parametrize('change', ['text', 'menu', 'move', 'replace'])
def test_dom_change_invalidates_old_target_and_fresh_target_works(page, change):
    old = tools.get_page_elements()
    if change == 'text':
        page.locator('body').evaluate("el => el.append('New page state')")
    elif change == 'menu':
        page.locator('body').evaluate("el => el.insertAdjacentHTML('beforeend', '<div role=menu>New menu</div>')")
    elif change == 'move':
        page.get_by_role('button', name='Search with your voice').evaluate("el => el.style.left='400px'")
    else:
        page.get_by_role('button', name='Search with your voice').evaluate('el => el.replaceWith(el.cloneNode(true))')
    # Replacement with identical markup requires checking that the original handle is connected.
    result = tools.click_element(old['observation_id'], 'e1')
    assert not result['success']
    fresh = tools.get_page_elements()
    voice = next(e for e in fresh['elements'] if e['name'] == 'Search with your voice')
    assert fresh['observation_id'] != old['observation_id']
    assert tools.click_element(fresh['observation_id'], voice['id'])['action_executed']
    assert page.locator('dialog').is_visible()


def test_text_input_selection_and_dialog_close_evidence(page):
    result = tools.click_text('Filters')
    assert result['action_executed'] and result['verification']['status'] == 'observed_change'
    assert 'Filter options' in result['verification']['after']['text']
    typed = tools.type_text('Search', 'Nexa verification')
    assert any(e['value'] == 'Nexa verification' for e in typed['verification']['after']['controls'])
    page.get_by_role('button', name='Search with your voice').click()
    closed = tools.click_text('Close')
    assert closed['verification']['removed_dialogs']
    page.set_content('<button aria-label="Expand" aria-expanded="false" onclick="this.setAttribute(\'aria-expanded\',\'true\')">Expand</button>')
    selected = tools.click_text('Expand')
    assert selected['verification']['after']['controls'][0]['expanded'] == 'true'


def test_back_refresh_and_first_result_have_navigation_evidence(page):
    page.route('https://local.test/**', lambda route: route.fulfill(content_type='text/html', body='<a id="video-title" href="/video">Video</a>'))
    tools.open_browser_url('https://local.test/one')
    tools.open_browser_url('https://local.test/two')
    back = tools.go_back()
    assert back['verification']['url_changed'] and back['verification']['after']['url'].endswith('/one')
    refreshed = tools.refresh_page()
    assert refreshed['action_executed'] and 'document' in refreshed['verification']['changed_fields']
    page.locator('body').evaluate("el => el.innerHTML = '<ytd-video-renderer><a id=video-title href=/video>Video</a></ytd-video-renderer>'")
    video = tools.open_first_youtube_result()
    assert video['action_executed'] and video['verification']['after']['url'].endswith('/video')


def test_agent_recovers_from_noop_with_real_fresh_dom_and_verifies_dialog(page, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app import agent
    page.set_content('''<button>Settings</button>
        <button onclick="document.querySelector('dialog').showModal()">Open settings</button>
        <dialog><p>Settings options</p></dialog>''')
    observation_ids = []
    def call(name=None, **arguments):
        return SimpleNamespace(id='r', output=[] if name is None else [SimpleNamespace(
            type='function_call', name=name, arguments=json.dumps(arguments), call_id='c')], output_text='Settings opened')
    def decide(**request):
        if isinstance(request['input'], str):
            return call('get_page_elements')
        result = json.loads(request['input'][0]['output'])
        if result.get('observation_id'):
            observation_ids.append(result['observation_id'])
            target = 'Settings' if len(observation_ids) == 1 else 'Open settings'
            element = next(e for e in result['elements'] if e['name'] == target)
            return call('click_element', observation_id=result['observation_id'], element_id=element['id'])
        if result.get('verification', {}).get('status') == 'unverified':
            return call('get_page_elements')
        if result.get('verification', {}).get('new_dialogs'):
            return call('verify_outcome', field='dialogs', evidence='Settings options')
        assert result.get('outcome_verified')
        return call()
    create = Mock(side_effect=decide)
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    result = agent.run_agent('Open the Settings menu')
    assert result['success'] and result['outcome_verified']
    assert len(observation_ids) == 2 and observation_ids[0] != observation_ids[1]
    assert [a['tool'] for a in result['actions']] == [
        'get_page_elements', 'click_element', 'get_page_elements', 'click_element', 'verify_outcome']
    assert page.locator('dialog').is_visible()


def test_property_value_change_rejects_old_observation(page):
    observation = tools.get_page_elements()
    page.get_by_placeholder('Search').fill('Changed after observation')
    assert tools.click_element(observation['observation_id'], 'e1')['error'] == 'stale_observation'


def test_playwright_timeout_still_returns_structured_error(page):
    result = tools.click_text('Missing control')
    assert result['success'] is False and result['error'] == 'browser_timeout'


def test_fresh_dom_target_still_cannot_click_through_overlay(page):
    page.locator('body').evaluate("el => el.insertAdjacentHTML('beforeend', '<div style=\"position:fixed;inset:0;z-index:9999;background:white\"></div>')")
    fresh = tools.get_page_elements()
    voice = next(e for e in fresh['elements'] if e['name'] == 'Search with your voice')
    result = tools.click_element(fresh['observation_id'], voice['id'])
    assert not result['success'] and result['error'] == 'browser_timeout'
    assert not page.locator('dialog').is_visible()


TYPED_VALUE = 'Nexa verification test'


def typing_workflow(page):
    from app.action_workflow import ActionWorkflow
    page.set_content('<input aria-label="Find videos" placeholder="Search"><input aria-label="Find videos" placeholder="Other">')
    workflow = ActionWorkflow()
    pre = tools.get_page_elements()
    workflow.record({'tool': 'get_page_elements', 'arguments': {}, 'result': pre})
    assert page.get_by_placeholder('Search', exact=True).input_value() == ''
    typed = {'tool': 'type_text', 'arguments': {'field': 'Search', 'text': TYPED_VALUE},
             'result': tools.type_text('Search', TYPED_VALUE)}
    workflow.record(typed)
    return workflow, typed, pre


def observe_typing(workflow):
    fresh = {'tool': 'get_page_elements', 'arguments': {}, 'result': tools.get_page_elements()}
    workflow.record(fresh)
    return fresh


def test_exact_observed_target_value_verifies_without_model_citation(page):
    workflow, typed, pre = typing_workflow(page)
    fresh = observe_typing(workflow)
    control = next(c for c in fresh['result']['state']['controls']
                   if c['control_id'] == typed['result']['expected_outcome']['control_id'])
    assert control['name'] == 'Find videos' and control['value'] == TYPED_VALUE
    assert fresh['result']['observation_id'] != pre['observation_id']
    result = workflow.finish([typed, fresh], 'The input contains the requested text.')
    assert result['success'] and result['outcome_verified'] and workflow.pending is None
    evidence = typed['result']['verification']
    assert evidence['outcome_verified']
    assert evidence['outcome_evidence']['observation_id'] == fresh['result']['observation_id']
    assert page.get_by_placeholder('Search', exact=True).input_value() == TYPED_VALUE


@pytest.mark.parametrize('value', ['Wrong text', 'Nexa verification', '', 'nexa verification test', 'Nexa verification test '])
def test_wrong_partial_or_nonexact_latest_value_does_not_verify(page, value):
    workflow, typed, _ = typing_workflow(page)
    # The immediate post-fill snapshot was correct; the newer state must take precedence.
    page.get_by_placeholder('Search', exact=True).fill(value)
    fresh = observe_typing(workflow)
    result = workflow.finish([typed, fresh], 'Done')
    assert not result['success'] and not result['outcome_verified']
    assert workflow.pending is typed


def test_other_input_with_same_label_and_expected_value_cannot_verify_target(page):
    workflow, typed, _ = typing_workflow(page)
    page.get_by_placeholder('Search', exact=True).fill('Wrong target value')
    page.get_by_placeholder('Other', exact=True).fill(TYPED_VALUE)
    fresh = observe_typing(workflow)
    assert not workflow.finish([typed, fresh], 'Done')['success']
    wrong_control = next(c for c in fresh['result']['state']['controls'] if c['value'] == TYPED_VALUE)
    import json
    assert not workflow.verify_outcome('controls', json.dumps(wrong_control))['outcome_verified']


@pytest.mark.parametrize('reuse_id', [True, False])
def test_pre_action_observation_cannot_verify_typing_even_with_expected_value(page, reuse_id):
    import copy
    from app.action_workflow import ActionWorkflow
    page.set_content('<input placeholder="Search">')
    page.get_by_placeholder('Search').fill(TYPED_VALUE)
    pre = tools.get_page_elements()
    workflow = ActionWorkflow()
    workflow.record({'tool': 'get_page_elements', 'arguments': {}, 'result': pre})
    page.get_by_placeholder('Search').fill('')
    typed = {'tool': 'type_text', 'arguments': {'field': 'Search', 'text': TYPED_VALUE},
             'result': tools.type_text('Search', TYPED_VALUE)}
    # Simulate a missing post-action snapshot, leaving only the stale pre-action data.
    typed['result']['verification'].pop('after')
    typed['result']['verification'].pop('after_observed_at')
    workflow.record(typed)
    stale = copy.deepcopy(pre)
    if not reuse_id:
        stale['observation_id'] = 'previously-unseen-but-old-snapshot'
    workflow.record({'tool': 'get_page_elements', 'arguments': {}, 'result': stale})
    result = workflow.finish([typed], 'Done')
    assert not result['success'] and not result['outcome_verified']


def test_typing_dispatch_without_post_action_evidence_stays_unverified(page):
    workflow, typed, _ = typing_workflow(page)
    typed['result']['verification'].pop('after')
    typed['result']['verification'].pop('after_observed_at')
    result = workflow.finish([typed], 'Done')
    assert typed['result']['action_executed']
    assert not result['success'] and not result['outcome_verified']


def test_type_snapshot_can_verify_without_separate_inspection_call(page):
    workflow, typed, _ = typing_workflow(page)
    result = workflow.finish([typed], 'The input contains the requested text.')
    assert result['success'] and result['outcome_verified']


def test_wrong_value_cannot_be_verified_by_citing_its_changed_record(page):
    import json
    workflow, typed, _ = typing_workflow(page)
    page.get_by_placeholder('Search', exact=True).fill('Nexa verification')
    fresh = observe_typing(workflow)
    changed = next(c for c in fresh['result']['state']['controls']
                   if c['control_id'] == typed['result']['expected_outcome']['control_id'])
    result = workflow.verify_outcome('controls', json.dumps(changed))
    assert not result['success'] and not result['outcome_verified']


def test_exact_value_verifies_with_minimal_citation_and_survives_later_observation(page):
    import json
    workflow, typed, _ = typing_workflow(page)
    observe_typing(workflow)
    verified = workflow.verify_outcome('controls', json.dumps({'name': 'Find videos', 'value': TYPED_VALUE}))
    assert verified['success'] and verified['outcome_verified']
    fresh = observe_typing(workflow)
    result = workflow.finish([typed, fresh], 'The input contains the requested text.')
    assert result['success'] and result['outcome_verified']


def test_replaced_control_with_same_value_does_not_impersonate_typed_target(page):
    workflow, typed, _ = typing_workflow(page)
    page.get_by_placeholder('Search', exact=True).evaluate('el => el.replaceWith(el.cloneNode(true))')
    fresh = observe_typing(workflow)
    assert page.get_by_placeholder('Search', exact=True).input_value() == TYPED_VALUE
    assert not workflow.finish([typed, fresh], 'Done')['success']


def test_missing_inline_snapshot_can_be_verified_by_later_fresh_observation(page):
    workflow, typed, _ = typing_workflow(page)
    typed['result']['verification'].pop('after')
    typed['result']['verification'].pop('after_observed_at')
    assert not workflow.complete_expected_outcome()
    fresh = observe_typing(workflow)
    result = workflow.finish([typed, fresh], 'The input contains the requested text.')
    assert result['success'] and result['outcome_verified']


def test_control_property_verifier_supports_observed_toggle_state(page):
    # Exercise the shared predicate evaluator with a real observed property change.
    # Existing click tools still use semantic verification unless they have an explicit expectation.
    import time
    from app.action_workflow import ActionWorkflow
    from app.browser_observation import CONTROL_DETAILS
    page.set_content('<button aria-label="Expand" aria-expanded="false" onclick="this.setAttribute(\'aria-expanded\',\'true\')">Expand</button>')
    control = page.get_by_role('button', name='Expand').evaluate(CONTROL_DETAILS)
    expected = {'kind': 'control_property', 'control_id': control['control_id'],
                'property': 'expanded', 'equals': 'true',
                'document': page.evaluate('performance.timeOrigin'), 'created_at': time.monotonic()}
    clicked = tools.click_text('Expand')
    clicked['expected_outcome'] = expected
    action = {'tool': 'click_text', 'arguments': {'text': 'Expand'}, 'result': clicked}
    workflow = ActionWorkflow()
    workflow.record(action)
    fresh = observe_typing(workflow)
    result = workflow.finish([action, fresh], 'Expanded')
    assert result['success'] and result['outcome_verified']


def test_failed_read_is_not_hidden_by_exact_typing_verification(page):
    workflow, typed, _ = typing_workflow(page)
    fresh = observe_typing(workflow)
    failure = {'tool': 'read_page', 'arguments': {}, 'result': {'success': False}}
    workflow.record(failure)
    result = workflow.finish([typed, fresh, failure], 'Done')
    assert not result['success'] and not result['outcome_verified']
