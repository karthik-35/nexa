"""Persistent Chromium lifecycle tests; temporary profile and intercepted local HTML only."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright, Error
from app import agent, browser_tools as tools

URL = 'https://www.youtube.com/'
HTML = '<title>Local YouTube fixture</title><input aria-label="Search" placeholder="Search">'


@pytest.fixture
def session(tmp_path, monkeypatch):
    for name in ('playwright', 'context', 'page', '_observation', '_retired_session'):
        monkeypatch.setattr(tools, name, None)
    monkeypatch.setattr(tools, '_context_closed', False)
    monkeypatch.setattr(tools, 'PROFILE_PATH', tmp_path / 'profile')
    monkeypatch.setattr(tools, '_lifecycle_events', [])
    contexts, drivers, launches, stopped = [], [], [], set()

    def start():
        driver = sync_playwright().start()
        drivers.append(driver)
        def launch(**kwargs):
            # A second live owner is a test failure, even if it has another context/page.
            assert all(i in stopped or not ctx.browser.is_connected() for i, ctx in enumerate(contexts))
            assert kwargs['user_data_dir'] == str(tmp_path / 'profile')
            launches.append(dict(kwargs))
            kwargs['headless'] = True
            ctx = driver.chromium.launch_persistent_context(**kwargs)
            contexts.append(ctx)
            ctx.route('https://www.youtube.com/**', lambda route: route.fulfill(content_type='text/html', body=HTML))
            return ctx
        def stop():
            driver.stop()
            stopped.add(drivers.index(driver))
        return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=stop)

    monkeypatch.setattr(tools, 'sync_playwright', lambda: SimpleNamespace(start=start))
    yield SimpleNamespace(contexts=contexts, drivers=drivers, launches=launches)
    tools.close_browser()


def assert_opened(result):
    assert result['success'] and result['action_executed']
    assert result['url'] == URL
    assert result['verification']['after']['url'] == URL
    assert tools.page.evaluate('location.href') == URL


def test_closed_cached_page_reacquires_live_sibling_without_new_browser(session):
    old = tools.get_page()
    sibling = tools.context.new_page()
    old.close()
    assert_opened(tools.open_browser_url(URL))
    assert tools.page is sibling
    assert len(session.launches) == 1


@pytest.mark.parametrize('close', ['page', 'context', 'browser', 'driver'])
def test_closed_target_or_user_closed_window_recovers(session, close):
    old = tools.get_page()
    observation = tools.get_page_elements()
    old_context = tools.context
    if close == 'page':
        old.close()  # Closing the last window/tab is the user-close equivalent.
    elif close == 'context':
        old_context.close()
    elif close == 'browser':
        old_context.browser.close()
    else:
        tools.playwright.stop()  # Non-null wrappers whose transport/loop is dead.
    result = tools.open_browser_url(URL)
    assert_opened(result)
    assert tools.page is not old
    assert result['browser_recovery']['reinitialized']
    assert len(session.launches) <= 2
    assert not tools.click_element(observation['observation_id'], 'e1')['success']


def test_close_browser_is_idempotent_and_can_start_same_profile_again(session):
    assert_opened(tools.open_browser_url(URL))
    tools.close_browser()
    tools.close_browser()
    assert tools.page is tools.context is tools.playwright is None
    assert_opened(tools.open_browser_url(URL))
    assert len(session.launches) == 2


@pytest.mark.parametrize('phase', ['observation', 'navigation'])
def test_target_dies_between_liveness_check_and_navigation_retries_once(session, monkeypatch, phase):
    old = tools.get_page()
    if phase == 'observation':
        original = tools.visible_state
        def snapshot(current):
            if current is old:
                old.context.close()
            return original(current)
        monkeypatch.setattr(tools, 'visible_state', snapshot)
    else:
        def die(*args, **kwargs):
            old.context.close()
            raise Error('Page.goto: Target page, context or browser has been closed')
        monkeypatch.setattr(old, 'goto', die)
    result = tools.open_browser_url(URL)
    assert_opened(result)
    assert len(session.launches) == 2
    assert 'retrying_requested_navigation' in result['browser_recovery']['events']


def test_failed_browser_launch_is_bounded_and_structured(session, monkeypatch):
    launch = Mock(side_effect=Error('Chromium launch failed'))
    stop = Mock()
    monkeypatch.setattr(tools, 'sync_playwright', lambda: SimpleNamespace(start=lambda: SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch), stop=stop)))
    result = tools.open_browser_url(URL)
    assert not result['success'] and result['action_executed'] is False
    assert result['error'] == 'browser_unavailable' and result['failure_kind'] == 'infrastructure'
    assert launch.call_count == 2 and stop.call_count == 2
    assert tools.page is tools.context is tools.playwright is None


def test_cleanup_failure_detaches_references_and_blocks_duplicate_launch(monkeypatch):
    ctx = SimpleNamespace(close=Mock(side_effect=Error('Cleanup failed')))
    driver = SimpleNamespace(stop=Mock(side_effect=Error('Driver failed')))
    for name, value in [('context', ctx), ('playwright', driver), ('page', object()),
                        ('_retired_session', None), ('_observation', None), ('_context_closed', False)]:
        monkeypatch.setattr(tools, name, value)
    start = Mock()
    monkeypatch.setattr(tools, 'sync_playwright', start)
    with pytest.raises(tools.BrowserUnavailable):
        tools.close_browser()
    assert tools.page is tools.context is tools.playwright is None
    assert ctx.close.call_count == driver.stop.call_count == 1
    result = tools.open_browser_url(URL)
    assert not result['success'] and result['failure_kind'] == 'infrastructure'
    start.assert_not_called()


@pytest.mark.parametrize('explicit_verification', [True, False, 'invalid'])
def test_full_agent_open_observe_type_observe_verify_with_dead_session(session, monkeypatch, explicit_verification):
    old = tools.get_page()
    old.context.browser.close()
    observation_ids = []
    typed = False
    def call(name=None, **arguments):
        return SimpleNamespace(id='response', output=[] if name is None else [SimpleNamespace(
            type='function_call', name=name, arguments=json.dumps(arguments), call_id='call')],
            output_text='The Search box contains Nexa verification test.')
    def decide(**request):
        nonlocal typed
        if isinstance(request['input'], str):
            return call('open_browser_url', url=URL)
        result = json.loads(request['input'][0]['output'])
        if result.get('browser') == 'nexa':
            assert result['success']
            return call('get_page_elements')
        if result.get('observation_id'):
            observation_ids.append(result['observation_id'])
            if not typed:
                assert any(e['name'] == 'Search' for e in result['elements'])
                return call('type_text', field='Search', text='Nexa verification test')
            control = next(e for e in result['state']['controls'] if e['value'] == 'Nexa verification test')
            if explicit_verification == 'invalid':
                return call('verify_outcome', field='value', evidence=control['value'])
            return call('verify_outcome', field='controls', evidence=json.dumps(control)) if explicit_verification else call()
        if result.get('action_executed'):
            typed = True
            return call('get_page_elements')
        if explicit_verification != 'invalid':
            assert result.get('outcome_verified'), result
        return call()
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=decide))))
    result = agent.run_agent('Open YouTube, inspect and type Nexa verification test without submitting, then verify.')
    assert result['success'] and result['outcome_verified']
    assert [a['tool'] for a in result['actions']] == ['open_browser_url', 'get_page_elements', 'type_text', 'get_page_elements'] + (['verify_outcome'] if explicit_verification else [])
    assert len(observation_ids) == 2 and observation_ids[0] != observation_ids[1]
    assert result['actions'][0]['result']['verification']['outcome_verified']
    assert tools.page.get_by_placeholder('Search').input_value() == 'Nexa verification test'
    assert tools.page.url == URL and len(session.launches) == 2


def test_wrong_cached_page_is_not_reused_outside_owned_context(session, monkeypatch):
    owned = tools.get_page()
    foreign_context = tools.context.browser.new_context()
    foreign_page = foreign_context.new_page()
    monkeypatch.setattr(tools, 'page', foreign_page)
    assert_opened(tools.open_browser_url(URL))
    assert tools.page is owned and foreign_page.url == 'about:blank'
    assert len(session.launches) == 1
    foreign_context.close()


def test_mid_navigation_closure_reports_unknown_dispatch_after_retry_exhaustion(session, monkeypatch):
    original = tools.get_page
    def doomed():
        current = original()
        def close(*args, **kwargs):
            current.context.close()
            raise Error('Page.goto: Target page, context or browser has been closed')
        monkeypatch.setattr(current, 'goto', close)
        return current
    monkeypatch.setattr(tools, 'get_page', doomed)
    result = tools.open_browser_url(URL)
    assert not result['success'] and result['failure_kind'] == 'infrastructure'
    assert result['action_executed'] is None
    assert result['browser_recovery']['reinitialized'] is False
    assert len(session.launches) == 2


def test_new_browser_observation_cannot_verify_old_typing_snapshot(session):
    from app.action_workflow import ActionWorkflow
    assert_opened(tools.open_browser_url(URL))
    workflow = ActionWorkflow()
    typed = {'tool': 'type_text', 'arguments': {'field': 'Search', 'text': 'Nexa verification test'},
             'result': tools.type_text('Search', 'Nexa verification test')}
    workflow.record(typed)
    assert typed['result']['verification']['after']['controls'][0]['value'] == 'Nexa verification test'
    tools.context.browser.close()
    fresh = {'tool': 'get_page_elements', 'arguments': {}, 'result': tools.get_page_elements()}
    assert fresh['result']['success'] and fresh['result']['browser_recovery']['reinitialized']
    assert fresh['result']['state']['url'] == 'about:blank'
    workflow.record(fresh)
    result = workflow.finish([typed, fresh], 'Done')
    assert not result['success'] and not result['outcome_verified']
