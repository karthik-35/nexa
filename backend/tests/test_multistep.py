"""Real browser state plus scripted intent/tool reasoning. No live sites or model calls."""
import html
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from playwright.sync_api import sync_playwright
from app import agent, browser_tools as tools
from app.ai import TaskPlan, TaskStep


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def site(browser, monkeypatch):
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(300)
    page.set_content('<body>Start</body>')
    monkeypatch.setattr(tools, 'get_page', lambda: page)
    requests = []
    settings = {'noop_first': False, 'noop_all': False, 'tracking_mode': None, 'redirect_video_id': None}
    def route(request):
        url = urlsplit(request.request.url)
        requests.append(request.request.url)
        query_name = 'search_query' if url.hostname == 'www.youtube.com' else 'q'
        query = parse_qs(url.query).get(query_name, [''])[0]
        box = f'<form action="/results"><input aria-label="Search" placeholder="Search" name="{query_name}" value="{html.escape(query, quote=True)}"></form>'
        if url.path == '/results':
            destination = '/watch?v=first&source=' + quote(query) if url.hostname == 'www.youtube.com' else '/org/fastapi'
            if settings['tracking_mode']:
                destination = '/watch?v=iWS9ogMPOI0&pp=tracking' if url.hostname == 'www.youtube.com' else '/org/fastapi?id=123&utm_source=fixture'
            def link(label, noop):
                return ('<a id="video-title" ' if label == 'First result' else '<a ') + f'href="{destination}" ' + ('onclick="event.preventDefault()"' if noop else '') + f'>{label}</a>'
            body = box + '<h1>Results for ' + html.escape(query) + '</h1><ytd-video-renderer>'
            body += link('First result', settings['noop_first'] or settings['noop_all'])
            body += link('Open first result', settings['noop_all']) + '</ytd-video-renderer>'
        elif url.path in {'/watch', '/org/fastapi'}:
            canonical = ''
            if settings['tracking_mode'] == 'normalize' and 'pp' in parse_qs(url.query):
                video_id = settings['redirect_video_id'] or parse_qs(url.query)['v'][0]
                canonical = '<script>history.replaceState(null, "", ' + json.dumps('/watch?v=' + video_id) + ')</script>'
            elif settings['tracking_mode'] == 'normalize' and 'utm_source' in parse_qs(url.query):
                canonical = '<script>history.replaceState(null, "", "/org/fastapi?id=123")</script>'
            body = canonical + '<title>Python FastAPI Tutorial: Build a REST API in 15 Minutes - YouTube</title><h1>First destination page</h1>'
        else:
            body = '<title>Search site</title>' + box
        request.fulfill(content_type='text/html', body=body)
    page.route('https://www.youtube.com/**', route)
    page.route('https://github.com/**', route)
    yield SimpleNamespace(page=page, requests=requests, settings=settings)
    tools.clear_observation()
    context.close()


def plan_for(url='https://www.youtube.com/', query='AI agents', result=True, back=False, again=None):
    steps = [TaskStep(kind='navigate', description='Open requested website', value=url),
             TaskStep(kind='type', description=f'Enter {query}', value=query),
             TaskStep(kind='submit', description=f'Search for {query}', value=query)]
    if result:
        steps.append(TaskStep(kind='activate', description='Open first result', value=None))
    if back:
        steps.append(TaskStep(kind='back', description='Return to results', value=None))
    if again:
        steps.extend([TaskStep(kind='type', description=f'Enter {again}', value=again),
                      TaskStep(kind='submit', description=f'Search for {again}', value=again)])
    return TaskPlan(steps=steps)


def scripted(monkeypatch, plan, *, already=False, premature=False, stale=False, stop_on_failure=False):
    memory = {'failed': False, 'early': False, 'old': None, 'stale_sent': False}
    count = 0
    def call(name=None, **arguments):
        nonlocal count
        count += 1
        return SimpleNamespace(id=str(count), output=[] if name is None else [SimpleNamespace(
            type='function_call', name=name, arguments=json.dumps(arguments), call_id=str(count))], output_text='Done')
    def decide(**request):
        if isinstance(request['input'], str):
            return call('get_page_elements') if already else call('open_browser_url', url=plan.steps[0].value)
        progress = json.loads(request['instructions'].split('\nTask progress (data): ')[1])
        step = progress['current_step']
        assert step is not None
        developer_retry = request['input'][0]['type'] if 'type' in request['input'][0] else None
        result = json.loads(request['input'][0]['output']) if developer_retry == 'function_call_output' else {}
        if premature and not memory['early'] and len(progress['completed_steps']) == 1:
            memory['early'] = True
            return call()
        if result.get('success') is False or result.get('verification', {}).get('status') == 'unverified':
            if result.get('error') != 'recovery_guard':
                memory['failed'] = True
        if stop_on_failure and memory['failed']:
            return call()
        if step['kind'] == 'type':
            if not result.get('observation_id'):
                return call('get_page_elements')
            memory['old'] = result['observation_id']
            return call('type_text', field='Search', text=step['value'])
        if step['kind'] == 'submit':
            if progress.get('unresolved_action'):
                return call('get_page_elements')
            return call('press_enter')
        if step['kind'] == 'activate':
            if stale and not memory['stale_sent']:
                memory['stale_sent'] = True
                return call('click_element', observation_id=memory['old'], element_id='e1')
            if not result.get('observation_id'):
                return call('get_page_elements')
            label = 'Open first result' if memory['failed'] else 'First result'
            target = next(e for e in result['elements'] if e['name'] == label)
            return call('click_element', observation_id=result['observation_id'], element_id=target['id'])
        if step['kind'] == 'back':
            return call('go_back')
        if step['kind'] == 'observe':
            return call('get_page_elements')
        raise AssertionError(step)
    create = Mock(side_effect=decide)
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    return create, parse


@pytest.mark.parametrize('result,back,again', [(False, False, None), (True, False, None), (True, True, None), (True, True, 'FastAPI tutorial')])
def test_ordered_search_result_back_and_second_search(site, monkeypatch, result, back, again):
    plan = plan_for(result=result, back=back, again=again)
    create, parse = scripted(monkeypatch, plan)
    outcome = agent.run_agent('Open YouTube, search for AI agents, open the first result, go back, then search FastAPI tutorial as requested by the ledger')
    assert outcome['success'] and outcome['outcome_verified'], outcome
    assert len(outcome['task']['completed_steps']) == len(plan.steps)
    assert not outcome['task']['work_remaining'] and outcome['task']['current_step'] is None
    assert outcome['task']['final_outcome_verified']
    assert [step['kind'] for step in outcome['task']['completed_steps']] == [step.kind for step in plan.steps]
    expected_query = again or 'AI agents'
    expected_path = '/watch' if result and not back else '/results'
    assert urlsplit(site.page.url).path == expected_path
    if expected_path == '/results':
        assert parse_qs(urlsplit(site.page.url).query)['search_query'] == [expected_query]
    assert all(call.kwargs['parallel_tool_calls'] is False for call in create.call_args_list)
    assert parse.call_count == 1
    assert len(outcome['actions']) < agent.MAX_TOOL_STEPS


def test_generic_github_search_and_repository_link(site, monkeypatch):
    plan = plan_for(url='https://github.com/', query='FastAPI')
    scripted(monkeypatch, plan)
    outcome = agent.run_agent('Open GitHub, search for FastAPI, and open the first repository result')
    assert outcome['success'], outcome
    assert site.page.url == 'https://github.com/org/fastapi'
    assert not any(a['tool'] in {'search_youtube', 'open_first_youtube_result'} for a in outcome['actions'])


def test_verified_prerequisite_does_not_end_task_on_premature_model_done(site, monkeypatch):
    create, _ = scripted(monkeypatch, plan_for(), premature=True)
    outcome = agent.run_agent('Open YouTube, search for AI agents, open first result')
    assert outcome['success'], outcome
    assert any(call.kwargs['input'][0].get('role') == 'developer' for call in create.call_args_list if isinstance(call.kwargs['input'], list))
    assert site.page.url.startswith('https://www.youtube.com/watch')


def test_failed_result_click_recovers_in_place_from_fresh_observation(site, monkeypatch):
    site.settings['noop_first'] = True
    scripted(monkeypatch, plan_for(back=True))
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result, then go back')
    assert outcome['success'], outcome
    names = [a['tool'] for a in outcome['actions']]
    assert names.count('open_browser_url') == names.count('type_text') == names.count('press_enter') == 1
    clicks = [i for i, a in enumerate(outcome['actions']) if a['tool'] == 'click_element']
    assert len(clicks) == 2
    assert outcome['actions'][clicks[0]]['result']['verification']['status'] == 'unverified'
    assert any(a['tool'] == 'get_page_elements' for a in outcome['actions'][clicks[0] + 1:clicks[1]])
    assert outcome['actions'][clicks[0]]['arguments']['observation_id'] != outcome['actions'][clicks[1]]['arguments']['observation_id']


def test_final_failure_reports_partial_progress_not_task_success(site, monkeypatch):
    site.settings['noop_all'] = True
    scripted(monkeypatch, plan_for(), stop_on_failure=True)
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result')
    assert not outcome['success'] and not outcome['outcome_verified']
    assert [s['kind'] for s in outcome['task']['completed_steps']] == ['navigate', 'type', 'submit']
    assert outcome['task']['current_step']['kind'] == 'activate'
    assert 'Could not verify: Open first result' in outcome['message']
    assert outcome['task']['work_remaining']


def test_old_target_after_submission_requires_new_observation(site, monkeypatch):
    scripted(monkeypatch, plan_for(), stale=True)
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result')
    assert outcome['success'], outcome
    rejected = next(a for a in outcome['actions'] if a['result'].get('error') == 'recovery_guard')
    assert 'fresh observation' in rejected['result']['message']
    valid = [a for a in outcome['actions'] if a['tool'] == 'click_element' and a['result'].get('action_executed')]
    assert len(valid) == 1 and valid[0]['arguments']['observation_id'] != rejected['arguments']['observation_id']


def test_already_open_site_and_exact_text_avoid_navigation_and_refill(site, monkeypatch):
    tools.open_browser_url('https://www.youtube.com/')
    site.page.get_by_placeholder('Search').fill('AI agents')
    site.page.evaluate("sessionStorage.inputEvents = '0'; document.querySelector('input').addEventListener('input', () => sessionStorage.inputEvents = String(Number(sessionStorage.inputEvents) + 1))")
    scripted(monkeypatch, plan_for(result=False), already=True)
    outcome = agent.run_agent('Open YouTube, search for AI agents')
    assert outcome['success'], outcome
    assert not any(a['tool'] == 'open_browser_url' for a in outcome['actions'])
    typed = next(a['result'] for a in outcome['actions'] if a['tool'] == 'type_text')
    assert typed['already_satisfied'] and typed['action_executed'] is False
    assert site.page.evaluate('sessionStorage.inputEvents') == '0'
    assert outcome['task']['completed_steps'][0]['status'] == 'already_satisfied'
    assert len([url for url in site.requests if urlsplit(url).path == '/']) == 1


def test_open_observe_type_verify_still_works_with_task_ledger(site, monkeypatch):
    plan = TaskPlan(steps=[TaskStep(kind='navigate', value='https://www.youtube.com/', description='Open site'),
                          TaskStep(kind='observe', value=None, description='Inspect page'),
                          TaskStep(kind='type', value='Nexa verification test', description='Type exact text')])
    scripted(monkeypatch, plan)
    outcome = agent.run_agent('Open YouTube, inspect, type Nexa verification test without submitting')
    assert outcome['success'] and outcome['outcome_verified'], outcome
    assert site.page.get_by_placeholder('Search').input_value() == 'Nexa verification test'
    assert urlsplit(site.page.url).path == '/'
    assert [a['tool'] for a in outcome['actions']] == ['open_browser_url', 'get_page_elements', 'type_text']


def test_repeated_no_progress_is_bounded(site, monkeypatch):
    plan = TaskPlan(steps=[TaskStep(kind='type', value='AI agents', description='Enter query')])
    response = SimpleNamespace(id='r', output=[SimpleNamespace(type='function_call', name='get_page_elements', arguments='{}', call_id='c')], output_text='')
    create = Mock(return_value=response)
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Type AI agents into Search')
    assert not outcome['success'] and len(outcome['actions']) == agent.MAX_TOOL_STEPS
    assert outcome['task']['work_remaining']


def test_specialized_search_reuses_current_site_and_existing_results(site):
    tools.open_browser_url('https://www.youtube.com/')
    searched = tools.search_youtube('AI agents')
    assert searched['success']
    again = tools.search_youtube('AI agents')
    assert again['already_satisfied'] and not again['action_executed']
    assert len([url for url in site.requests if urlsplit(url).path == '/']) == 1
    assert len([url for url in site.requests if urlsplit(url).path == '/results']) == 1


def test_repeated_premature_done_stops_with_work_remaining(monkeypatch):
    plan = plan_for()
    create = Mock(return_value=SimpleNamespace(id='r', output=[], output_text='Done'))
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result')
    assert not outcome['success'] and not outcome['outcome_verified']
    assert create.call_count == 3 and not outcome['actions']
    assert outcome['task']['remaining_steps'] == len(plan.steps)


def test_incomplete_intent_extraction_executes_no_tools(monkeypatch):
    create = Mock()
    parse = Mock(return_value=SimpleNamespace(status='incomplete', output_parsed=None))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Open YouTube and search FastAPI')
    assert not outcome['success'] and not outcome['actions']
    create.assert_not_called()


def test_typing_only_cannot_complete_search_step(site, monkeypatch):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    from app.ai import TaskPlan, TaskStep
    tools.open_browser_url('https://www.youtube.com/')
    workflow = ActionWorkflow()
    task = TaskProgress('Search for AI agents', TaskPlan(steps=[
        TaskStep(kind='type', description='Enter query', value='AI agents'),
        TaskStep(kind='submit', description='Submit search', value='AI agents')]))
    typed = {'tool': 'type_text', 'arguments': {'field': 'Search', 'text': 'AI agents'}, 'result': tools.type_text('Search', 'AI agents')}
    workflow.record(typed)
    task.record(typed)
    task.sync(workflow, [typed])
    result = task.finish(workflow.finish([typed], 'Done'), workflow)
    assert typed['result']['verification']['outcome_verified']
    assert not result['success'] and not result['outcome_verified']
    assert result['task']['current_step']['kind'] == 'submit'


def test_combined_search_tool_completes_type_and_submit_goals(site, monkeypatch):
    plan = plan_for(result=False)
    calls = [SimpleNamespace(id='one', output=[SimpleNamespace(type='function_call', name='open_browser_url',
              arguments='{"url":"https://www.youtube.com/"}', call_id='open')], output_text=''),
             SimpleNamespace(id='two', output=[SimpleNamespace(type='function_call', name='search_youtube',
              arguments='{"query":"AI agents"}', call_id='search')], output_text='')]
    create = Mock(side_effect=calls)
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Open YouTube and search for AI agents')
    assert outcome['success'], outcome
    assert [s['kind'] for s in outcome['task']['completed_steps']] == ['navigate', 'type', 'submit']
    assert len([url for url in site.requests if urlsplit(url).path == '/']) == 1


def test_recovery_exhaustion_preserves_completed_steps_and_stops(site, monkeypatch):
    plan = plan_for()
    site.settings['noop_all'] = True
    original_click = tools.click_element
    attempted = []
    def failed_click(**arguments):
        # Exercise the real no-op page click and its resulting evidence.
        attempted.append(arguments)
        return original_click(**arguments)
    monkeypatch.setitem(agent.TOOLS, 'click_element', failed_click)
    # Different observed controls keep the test focused on the attempt limit.
    # Use click_text alternatives on the same no-op results page; add a third real target.
    original_elements = tools.get_page_elements
    def inspect():
        if urlsplit(site.page.url).path == '/results' and not site.page.get_by_text('Third attempt', exact=True).count():
            site.page.locator('body').evaluate("el => el.insertAdjacentHTML('beforeend', '<a href=/watch?v=first onclick=event.preventDefault()>Third attempt</a>')")
        return original_elements()
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', inspect)
    create_base, parse = scripted(monkeypatch, plan)
    base_decide = create_base.side_effect
    labels = ['First result', 'Open first result', 'Third attempt']
    def decide(**request):
        if isinstance(request['input'], list) and request['input'][0].get('type') == 'function_call_output':
            result = json.loads(request['input'][0]['output'])
            if result.get('observation_id') and urlsplit(result.get('state', {}).get('url', '')).path == '/results':
                target = next(e for e in result['elements'] if e['name'] == labels[min(len(attempted), 2)])
                return SimpleNamespace(id='attempt', output=[SimpleNamespace(type='function_call', name='click_element',
                    arguments=json.dumps({'observation_id': result['observation_id'], 'element_id': target['id']}), call_id='try')], output_text='')
            if len(attempted) == 3:
                # One additional proposed action must be blocked by the existing attempt guard.
                return SimpleNamespace(id='stop', output=[SimpleNamespace(type='function_call', name='click_text',
                    arguments='{"text":"Third attempt"}', call_id='stop')], output_text='')
        return base_decide(**request)
    create_base.side_effect = decide
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result')
    assert not outcome['success'] and not outcome['outcome_verified']
    assert len(attempted) == 3
    assert len(outcome['task']['completed_steps']) == 3
    assert outcome['task']['current_step']['kind'] == 'activate'
    assert sum(a['tool'] == 'open_browser_url' for a in outcome['actions']) == 1


def test_literal_query_drift_is_rejected_before_dispatch(site, monkeypatch):
    from app.task_progress import TaskProgress
    task = TaskProgress('Type FastAPI', TaskPlan(steps=[TaskStep(kind='type', description='Enter query', value='FastAPI')]))
    assert 'exact literal' in task.guard('type_text', {'field': 'Search', 'text': 'unrelated'})
    assert 'before submitting' in task.guard('press_enter', {})


def test_already_on_site_results_can_satisfy_website_prerequisite(site, monkeypatch):
    tools.open_browser_url('https://www.youtube.com/results?search_query=old')
    scripted(monkeypatch, plan_for(result=False), already=True)
    outcome = agent.run_agent('Open YouTube and search AI agents')
    assert outcome['success'], outcome
    assert not any(a['tool'] == 'open_browser_url' for a in outcome['actions'])
    assert outcome['task']['completed_steps'][0]['status'] == 'already_satisfied'


def test_search_tool_can_satisfy_named_website_prerequisite_from_its_verified_state(site, monkeypatch):
    plan = plan_for(result=False)
    create = Mock(return_value=SimpleNamespace(id='search', output=[SimpleNamespace(type='function_call',
        name='search_youtube', arguments='{"query":"AI agents"}', call_id='search')], output_text=''))
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Search YouTube for AI agents')
    assert outcome['success'] and outcome['outcome_verified'], outcome
    assert len(outcome['actions']) == 1 and len(outcome['task']['completed_steps']) == 3


def test_another_website_cannot_supply_search_completion_evidence(site):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    task = TaskProgress('Open GitHub and search FastAPI', plan_for(url='https://github.com/', query='FastAPI', result=False))
    workflow = ActionWorkflow()
    opened = {'tool': 'open_browser_url', 'arguments': {'url': 'https://github.com/'}, 'result': tools.open_browser_url('https://github.com/')}
    workflow.record(opened)
    task.record(opened)
    task.sync(workflow, [opened])
    wrong_site = {'tool': 'search_youtube', 'arguments': {'query': 'FastAPI'}, 'result': tools.search_youtube('FastAPI')}
    workflow.record(wrong_site)
    task.record(wrong_site)
    task.sync(workflow, [opened, wrong_site])
    result = task.finish(workflow.finish([opened, wrong_site], 'Done'), workflow)
    assert not result['success'] and result['task']['current_step']['kind'] == 'type'


def test_refresh_read_and_vision_inspection_remain_available_mid_task(site, monkeypatch):
    plan = TaskPlan(steps=[TaskStep(kind='navigate', value='https://www.youtube.com/', description='Open site'),
                          TaskStep(kind='observe', value=None, description='Read current page'),
                          TaskStep(kind='refresh', value=None, description='Refresh page'),
                          TaskStep(kind='observe', value=None, description='Inspect refreshed screen'),
                          TaskStep(kind='type', value='FastAPI', description='Enter query')])
    stages = [('open_browser_url', {'url': 'https://www.youtube.com/'}), ('read_page', {}),
              ('refresh_page', {}), ('analyze_current_screen', {}), ('type_text', {'field': 'Search', 'text': 'FastAPI'})]
    def response(name, arguments):
        return SimpleNamespace(id=name, output=[SimpleNamespace(type='function_call', name=name,
            arguments=json.dumps(arguments), call_id=name)], output_text='')
    create = Mock(side_effect=[response(*stage) for stage in stages])
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    monkeypatch.setattr(tools, 'analyze_screenshot', lambda *args, **kwargs: {'page': 'Search site', 'summary': 'Search input', 'elements': []})
    outcome = agent.run_agent('Open YouTube, read it, refresh, inspect the screen, type FastAPI')
    assert outcome['success'], outcome
    assert site.page.get_by_placeholder('Search').input_value() == 'FastAPI'
    assert [a['tool'] for a in outcome['actions']] == [stage[0] for stage in stages]


def test_scroll_and_stop_remain_supported_task_outcomes(site, monkeypatch):
    site.page.set_content('<body style="height:3000px">Scrollable page</body>')
    plan = TaskPlan(steps=[TaskStep(kind='scroll', description='Scroll down', value=None),
                          TaskStep(kind='stop', description='Stop scrolling', value=None)])
    def response(name, args):
        return SimpleNamespace(id=name, output=[SimpleNamespace(type='function_call', name=name, arguments=json.dumps(args), call_id=name)], output_text='')
    phase = 0
    def decide(**request):
        nonlocal phase
        phase += 1
        if phase == 1:
            return response('scroll_page', {'direction': 'down'})
        output = json.loads(request['input'][0]['output'])
        if phase in {2, 4}:
            return response('verify_outcome', {'field': 'scroll', 'evidence': json.dumps(output['verification']['after']['scroll'], sort_keys=True)})
        return response('stop_scrolling', {})
    create = Mock(side_effect=decide)
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Scroll down then stop scrolling')
    assert outcome['success'], outcome
    assert not site.page.evaluate('window.__nexaScrolling')
    assert [s['kind'] for s in outcome['task']['completed_steps']] == ['scroll', 'stop']


def test_specialized_result_failure_can_recover_using_fresh_generic_target(site, monkeypatch):
    site.settings['noop_first'] = True
    plan = plan_for()
    create_base, _ = scripted(monkeypatch, plan)
    original = create_base.side_effect
    attempted = False
    def decide(**request):
        nonlocal attempted
        proposed = original(**request)
        if proposed.output and proposed.output[0].name == 'click_element' and not attempted:
            attempted = True
            return SimpleNamespace(id='special', output=[SimpleNamespace(type='function_call', name='open_first_youtube_result', arguments='{}', call_id='special')], output_text='')
        return proposed
    create_base.side_effect = decide
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result')
    assert outcome['success'], outcome
    assert any(a['tool'] == 'open_first_youtube_result' and not a['result']['verification']['outcome_verified'] for a in outcome['actions'])
    assert any(a['tool'] == 'click_element' and a['result']['verification']['outcome_verified'] for a in outcome['actions'])
    assert sum(a['tool'] == 'open_browser_url' for a in outcome['actions']) == 1


def test_verified_read_task_still_generates_requested_summary_without_more_tools(site, monkeypatch):
    site.page.set_content('<h1>FastAPI guide</h1><p>This guide explains dependency injection.</p>')
    plan = TaskPlan(steps=[TaskStep(kind='observe', description='Summarize the current page', value=None)])
    calls = [SimpleNamespace(id='read', output=[SimpleNamespace(type='function_call', name='read_page', arguments='{}', call_id='read')], output_text=''),
             SimpleNamespace(id='summary', output=[], output_text='The guide explains FastAPI dependency injection.')]
    create = Mock(side_effect=calls)
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create, parse=parse)))
    outcome = agent.run_agent('Summarize the current page')
    assert outcome['success'] and outcome['message'] == 'The guide explains FastAPI dependency injection.'
    assert create.call_args_list[-1].kwargs['tools'] == []
    assert 'dependency injection' in json.loads(create.call_args_list[-1].kwargs['input'][0]['output'])['content']
    assert len(outcome['actions']) == 1


def test_later_back_action_cannot_execute_before_requested_result_step(site, monkeypatch):
    create, _ = scripted(monkeypatch, plan_for(back=True))
    original = create.side_effect
    injected = False
    def decide(**request):
        nonlocal injected
        if not isinstance(request['input'], str):
            progress = json.loads(request['instructions'].split('\nTask progress (data): ')[1])
            if progress['current_step']['kind'] == 'activate' and not injected:
                injected = True
                return SimpleNamespace(id='out-of-order', output=[SimpleNamespace(type='function_call', name='go_back', arguments='{}', call_id='early-back')], output_text='')
        return original(**request)
    create.side_effect = decide
    outcome = agent.run_agent('Open YouTube, search AI agents, open first result, then go back')
    assert outcome['success'], outcome
    backs = [a for a in outcome['actions'] if a['tool'] == 'go_back']
    assert len(backs) == 2 and backs[0]['result']['error'] == 'recovery_guard'
    assert backs[1]['result']['verification']['outcome_verified']
    assert urlsplit(site.page.url).path == '/results'


def test_one_search_action_cannot_consume_two_distinct_submission_goals(site):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    tools.open_browser_url('https://www.youtube.com/')
    plan = TaskPlan(steps=[TaskStep(kind='submit', description='First submission', value='AI agents'),
                          TaskStep(kind='submit', description='Second submission', value='AI agents')])
    task = TaskProgress('Submit the query twice', plan)
    workflow = ActionWorkflow()
    action = {'tool': 'search_youtube', 'arguments': {'query': 'AI agents'}, 'result': tools.search_youtube('AI agents')}
    workflow.record(action)
    task.record(action)
    task.sync(workflow, [action])
    task.sync(workflow, [action])
    assert len(task.completed) == 1 and task.current.description == 'Second submission'
    assert not task.finish(workflow.finish([action], 'Done'), workflow)['success']


def test_tracked_first_result_clears_click_and_unlocks_back(site):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    site.settings['tracking_mode'] = 'normalize'
    plan = plan_for(query='FastAPI tutorial', back=True)
    workflow, progress, actions = ActionWorkflow(), TaskProgress('Open, search, open result, back', plan), []
    def execute(name, **arguments):
        assert workflow.guard(name, arguments) is None
        assert progress.guard(name, arguments) is None
        result = agent.TOOLS[name](**arguments)
        action = {'tool': name, 'arguments': arguments, 'result': result}
        actions.append(action)
        workflow.record(action)
        progress.record(action)
        progress.sync(workflow, actions)
        return result
    execute('open_browser_url', url='https://www.youtube.com/')
    execute('get_page_elements')
    execute('type_text', field='Search', text='FastAPI tutorial')
    execute('press_enter')
    site.page.wait_for_url('**/results?search_query=**')
    fresh = execute('get_page_elements')
    target = next(e for e in fresh['elements'] if e['name'] == 'First result')
    clicked = execute('click_element', observation_id=fresh['observation_id'], element_id=target['id'])
    assert clicked.get('success'), clicked
    assert clicked['expected_outcome']['equals'].endswith('v=iWS9ogMPOI0&pp=tracking')
    assert clicked['verification']['after']['url'] == 'https://www.youtube.com/watch?v=iWS9ogMPOI0'
    assert clicked['verification']['outcome_verified']
    assert workflow.pending is None and progress.current.kind == 'back'
    assert progress.completed[-1]['kind'] == 'activate'
    execute('go_back')
    outcome = progress.finish(workflow.finish(actions, 'Completed'), workflow)
    assert outcome['success'] and outcome['outcome_verified']
    assert outcome['task']['final_outcome_verified'] and outcome['task']['current_step'] is None
    assert not outcome['task']['work_remaining']
    assert site.page.url == 'https://www.youtube.com/results?search_query=FastAPI+tutorial'


def test_full_agent_normalized_video_destination_then_back(site, monkeypatch):
    site.settings['tracking_mode'] = 'normalize'
    scripted(monkeypatch, plan_for(query='FastAPI tutorial', back=True))
    outcome = agent.run_agent('Open YouTube, search for "FastAPI tutorial", open the first result, then go back to the search results.')
    assert outcome['success'] and outcome['outcome_verified'], outcome
    assert outcome['task']['final_outcome_verified']
    assert outcome['task']['current_step'] is None and not outcome['task']['work_remaining']
    assert [s['kind'] for s in outcome['task']['completed_steps']] == ['navigate', 'type', 'submit', 'activate', 'back']
    clicked = next(a['result'] for a in outcome['actions'] if a['tool'] == 'click_element')
    assert clicked['expected_outcome']['equals'] == 'https://www.youtube.com/watch?v=iWS9ogMPOI0&pp=tracking'
    assert clicked['verification']['after']['url'] == 'https://www.youtube.com/watch?v=iWS9ogMPOI0'
    assert clicked['verification']['after']['title'] == 'Python FastAPI Tutorial: Build a REST API in 15 Minutes - YouTube'
    assert clicked['verification']['outcome_verified']
    assert clicked['verification']['outcome_evidence']['match'] == 'destination_equivalent'
    back = next(a for a in outcome['actions'] if a['tool'] == 'go_back')
    assert back['result']['verification']['outcome_verified']
    assert site.page.url == 'https://www.youtube.com/results?search_query=FastAPI+tutorial'
    assert not any(a['result'].get('error') == 'recovery_guard' for a in outcome['actions'])


def test_different_video_identity_does_not_complete_activate_or_unlock_back(site):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    site.settings.update(tracking_mode='normalize', redirect_video_id='wrong-video')
    tools.open_browser_url('https://www.youtube.com/results?search_query=FastAPI+tutorial')
    fresh = tools.get_page_elements()
    target = next(e for e in fresh['elements'] if e['name'] == 'First result')
    workflow = ActionWorkflow()
    progress = TaskProgress('Open first result then go back', TaskPlan(steps=[
        TaskStep(kind='activate', description='Open first result', value=None),
        TaskStep(kind='back', description='Back to results', value=None)]))
    action = {'tool': 'click_element', 'arguments': {'observation_id': fresh['observation_id'], 'element_id': target['id']},
              'result': tools.click_element(fresh['observation_id'], target['id'])}
    workflow.record(action)
    progress.record(action)
    progress.sync(workflow, [action])
    assert not action['result']['verification']['outcome_verified']
    assert progress.current.kind == 'activate' and workflow.pending is action
    assert workflow.guard('go_back', {}) is not None
    # New title and URL alone cannot override the wrong stable video identity.
    assert not workflow.verify_outcome('url', site.page.url)['success']
    assert not workflow.verify_outcome('title', site.page.title())['success']
    assert not progress.finish(workflow.finish([action], 'Done'), workflow)['success']


def test_back_history_preserves_results_when_video_url_normalizes_after_verification(site):
    from app.action_workflow import ActionWorkflow
    from app.task_progress import TaskProgress
    site.settings['tracking_mode'] = 'retain'
    workflow = ActionWorkflow()
    progress = TaskProgress('Open result then back', TaskPlan(steps=[
        TaskStep(kind='activate', description='Open first result', value=None),
        TaskStep(kind='back', description='Return to results', value=None)]))
    tools.open_browser_url('https://www.youtube.com/results?search_query=FastAPI+tutorial')
    observation = tools.get_page_elements()
    target = next(e for e in observation['elements'] if e['name'] == 'First result')
    action = {'tool': 'click_element', 'arguments': {}, 'result': tools.click_element(observation['observation_id'], target['id'])}
    workflow.record(action)
    progress.record(action)
    progress.sync(workflow, [action])
    assert progress.current.kind == 'back' and progress.history[-1].endswith('&pp=tracking')
    site.page.evaluate("history.replaceState(null, '', '/watch?v=iWS9ogMPOI0')")
    assert workflow.guard('go_back', {}) is None and progress.guard('go_back', {}) is None
    back = {'tool': 'go_back', 'arguments': {}, 'result': tools.go_back()}
    workflow.record(back)
    progress.record(back)
    progress.sync(workflow, [action, back])
    assert progress.done and len(progress.history) == 1
    assert progress.history[0] == 'https://www.youtube.com/results?search_query=FastAPI+tutorial'


def test_generic_repository_link_tracking_normalization_and_back(site, monkeypatch):
    site.settings['tracking_mode'] = 'normalize'
    scripted(monkeypatch, plan_for(url='https://github.com/', query='FastAPI', back=True))
    outcome = agent.run_agent('Open GitHub, search for FastAPI, open the first result, then go back')
    assert outcome['success'] and outcome['task']['final_outcome_verified'], outcome
    clicked = next(a['result'] for a in outcome['actions'] if a['tool'] == 'click_element')
    assert clicked['expected_outcome']['equals'] == 'https://github.com/org/fastapi?id=123&utm_source=fixture'
    assert clicked['verification']['after']['url'] == 'https://github.com/org/fastapi?id=123'
    assert clicked['verification']['outcome_verified']
    assert site.page.url == 'https://github.com/results?q=FastAPI'


def test_back_does_not_treat_another_video_as_the_known_history_destination(site):
    from app.task_progress import TaskProgress
    progress = TaskProgress('Back to results', TaskPlan(steps=[TaskStep(kind='back', description='Back to results', value=None)]))
    progress.history = ['https://www.youtube.com/results?search_query=FastAPI+tutorial', 'https://www.youtube.com/watch?v=abc&pp=x']
    # Native Back can execute while the user's page has unexpectedly moved elsewhere.
    # A changed URL is insufficient to match the previously observed history.
    action = {'tool': 'go_back', 'arguments': {}, 'result': {'success': True, 'verification': {
        'outcome_verified': True, 'before': {'url': 'https://www.youtube.com/watch?v=other'},
        'after': {'url': 'https://www.youtube.com/results?search_query=FastAPI+tutorial'}}}}
    assert not progress.matches(progress.current, action)
    action['result']['verification']['before']['url'] = 'https://www.youtube.com/watch?v=abc'
    assert progress.matches(progress.current, action)
    action['result']['verification']['after']['url'] = 'https://www.youtube.com/results?search_query=unrelated'
    assert not progress.matches(progress.current, action)
