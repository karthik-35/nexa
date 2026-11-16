import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from playwright.sync_api import TimeoutError
from app import agent, mac_tools


def response(*calls, text='Done'):
    return SimpleNamespace(id='response-id', output=[
        SimpleNamespace(type='function_call', name=name, arguments=arguments, call_id=str(i))
        for i, (name, arguments) in enumerate(calls)
    ], output_text=text)


def model(monkeypatch, *responses):
    create = Mock(side_effect=responses)
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    return create


def dispatched(before, after, **extra):
    from app.browser_observation import state_evidence
    return {'success': True, 'action_executed': True,
            'verification': state_evidence({'url': before, 'dialogs': []}, {'url': after, 'dialogs': []}), **extra}


def verified_url(url):
    return response(('verify_outcome', json.dumps({'field': 'url', 'evidence': url})))


def test_multistep_preserves_sequential_tool_calls(monkeypatch):
    search = Mock(return_value=dispatched('home', 'results'))
    first = Mock(return_value=dispatched('results', 'video'))
    monkeypatch.setitem(agent.TOOLS, 'search_youtube', search)
    monkeypatch.setitem(agent.TOOLS, 'open_first_youtube_result', first)
    create = model(monkeypatch, response(('search_youtube', '{"query":"Python tutorials"}')),
                   verified_url('results'), response(('open_first_youtube_result', '{}')), verified_url('video'), response())
    result = agent.run_agent('Search YouTube for Python tutorials and open the first result')
    assert result['success']
    assert [a['tool'] for a in result['actions']] == ['search_youtube', 'verify_outcome', 'open_first_youtube_result', 'verify_outcome']
    search.assert_called_once_with(query='Python tutorials')
    first.assert_called_once_with()
    assert all(call.kwargs['parallel_tool_calls'] is False for call in create.call_args_list)


def test_browser_failure_is_returned_to_model_and_can_recover(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(side_effect=TimeoutError('Target missing')))
    monkeypatch.setitem(agent.TOOLS, 'read_page', Mock(return_value={'success': True, 'content': 'Filters'}))
    create = model(monkeypatch, response(('click_text', '{"text":"microphone icon"}')),
                   response(('read_page', '{}')), response(text='Target unavailable'))
    result = agent.run_agent('Click the microphone')
    assert len(result['actions']) == 2
    output = json.loads(create.call_args_list[1].kwargs['input'][0]['output'])
    assert output['success'] is False and 'Target missing' in output['message']


@pytest.mark.parametrize('arguments', ['{', '[]', '{"text":3}', '{"invented": "value"}', '{}'])
def test_bad_arguments_do_not_execute(monkeypatch, arguments):
    def click(text):
        raise AssertionError('Invalid arguments must not execute')
    monkeypatch.setitem(agent.TOOLS, 'click_text', click)
    model(monkeypatch, response(('click_text', arguments)), response(text='Unable'))
    assert agent.run_agent('click')['success'] is False


def test_unexpected_programmer_error_is_not_hidden(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'read_page', Mock(side_effect=TypeError('programmer bug')))
    model(monkeypatch, response(('read_page', '{}')))
    with pytest.raises(TypeError, match='programmer bug'):
        agent.run_agent('read')


def test_tool_allowlist(monkeypatch):
    model(monkeypatch, response(('shell', '{"command":"anything"}')))
    assert agent.run_agent('run')['success'] is False
    assert 'shell' not in agent.TOOLS


def test_final_answer_after_fifth_step(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'read_page', Mock(return_value={'success': True}))
    model(monkeypatch, *[response(('read_page', '{}')) for _ in range(5)], response(text='Finished'))
    result = agent.run_agent('read')
    assert result['success'] and result['message'] == 'Finished'


def test_spotify_uses_allowlist_and_no_shell(monkeypatch):
    run = Mock()
    monkeypatch.setattr(mac_tools.subprocess, 'run', run)
    assert mac_tools.open_app('Spotify')['success']
    run.assert_called_once_with(['open', '-a', 'Spotify'], check=True)
    assert mac_tools.open_app('arbitrary-command')['success'] is False
    assert run.call_count == 1


@pytest.mark.parametrize(('message', 'tool_name', 'browser'), [
    ('Open YouTube', 'open_browser_url', 'nexa'),
    ('Open YouTube in my default browser', 'open_website', 'default'),
])
def test_browser_routing_contract_and_dispatch(monkeypatch, message, tool_name, browser):
    # Scripted model responses test the tool wiring, not live model selection.
    controlled = Mock(return_value=dispatched('about:blank', 'https://www.youtube.com/', browser='nexa'))
    default = Mock(return_value={'success': True, 'browser': 'default'})
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', controlled)
    monkeypatch.setitem(agent.TOOLS, 'open_website', default)
    create = model(monkeypatch, response((tool_name, '{"url":"https://www.youtube.com"}')),
                   *([verified_url('https://www.youtube.com/')] if browser == 'nexa' else []), response())
    result = agent.run_agent(message)
    assert result['success']
    assert result['actions'][0]['tool'] == tool_name
    assert result['actions'][0]['result']['browser'] == browser
    chosen, unused = (controlled, default) if browser == 'nexa' else (default, controlled)
    chosen.assert_called_once_with(url='https://www.youtube.com')
    unused.assert_not_called()
    request = create.call_args_list[0].kwargs
    definitions = {tool['name']: tool for tool in request['tools']}
    assert definitions['open_browser_url']['parameters']['required'] == ['url']
    assert 'dedicated Playwright Chromium' in definitions['open_browser_url']['description']
    assert 'Only use when the user explicitly requests' in definitions['open_website']['description']
    assert "For 'Open YouTube', call open_browser_url" in request['instructions']
    assert 'open_website ONLY' in request['instructions']


def test_unverified_dispatch_cannot_claim_success_even_after_read(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(return_value=dispatched('home', 'home')))
    monkeypatch.setitem(agent.TOOLS, 'read_page', Mock(return_value={'success': True, 'content': 'Home'}))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')),
          response(('read_page', '{}')), response(text='Done, Settings is open!'))
    result = agent.run_agent('Open Settings')
    assert not result['success'] and not result['outcome_verified']
    assert 'could not be verified' in result['message'] and 'Settings is open' not in result['message']


def test_observed_change_requires_explicit_grounded_outcome(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(return_value=dispatched('home', 'settings')))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')), response(text='Done'))
    assert not agent.run_agent('Open Settings')['success']
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')), verified_url('invented'), response(text='Done'))
    assert not agent.run_agent('Open Settings')['success']
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')), verified_url('settings'), response(text='Settings opened'))
    result = agent.run_agent('Open Settings')
    assert result['success'] and result['outcome_verified']
    assert result['actions'][0]['result']['verification']['outcome_verified']


def observation(oid, *names):
    return {'success': True, 'observation_id': oid,
            'elements': [{'id': f'e{i}', 'name': name, 'text': name} for i, name in enumerate(names)]}


def test_recovery_requires_fresh_observation_then_accepts_alternative(monkeypatch):
    click = Mock(side_effect=[{'success': False, 'message': 'Target unavailable'}, dispatched('home', 'settings')])
    inspect_page = Mock(return_value=observation('fresh', 'Settings menu'))
    monkeypatch.setitem(agent.TOOLS, 'click_text', click)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', inspect_page)
    create = model(monkeypatch, response(('click_text', '{"text":"Settings"}')),
          response(('click_text', '{"text":"Settings menu"}')),
          response(('get_page_elements', '{}')),
          response(('click_text', '{"text":"Settings menu"}')), verified_url('settings'), response())
    result = agent.run_agent('Open Settings')
    assert result['success'] and click.call_count == 2
    assert result['actions'][1]['result']['error'] == 'recovery_guard'
    outputs = [json.loads(c.kwargs['input'][0]['output']) for c in create.call_args_list[1:]]
    assert 'fresh' in outputs[1]['message']


def test_recovery_dom_uses_new_observed_id(monkeypatch):
    inspect_page = Mock(side_effect=[observation('old', 'Settings'), observation('new', 'Open settings')])
    click = Mock(side_effect=[{'success': False}, dispatched('home', 'settings')])
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', inspect_page)
    monkeypatch.setitem(agent.TOOLS, 'click_element', click)
    model(monkeypatch, response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"old","element_id":"e0"}')),
          response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"new","element_id":"e0"}')),
          verified_url('settings'), response())
    assert agent.run_agent('Open Settings')['success']
    assert [c.kwargs['observation_id'] for c in click.call_args_list] == ['old', 'new']


def test_same_target_cannot_be_retried_with_new_observation_or_method(monkeypatch):
    click = Mock(return_value={'success': False})
    retry = Mock()
    monkeypatch.setitem(agent.TOOLS, 'click_text', click)
    monkeypatch.setitem(agent.TOOLS, 'click_element', retry)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(return_value=observation('fresh', 'Settings')))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')),
          response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"fresh","element_id":"e0"}')), response(text='Done'))
    result = agent.run_agent('Open Settings')
    assert not result['success']
    retry.assert_not_called()
    assert 'already attempted' in result['actions'][2]['result']['message']


def test_three_attempt_limit_stops_fourth_execution(monkeypatch):
    click = Mock(return_value={'success': False})
    monkeypatch.setitem(agent.TOOLS, 'click_text', click)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(side_effect=[observation(str(i), f'Option {i}') for i in range(3)]))
    calls = [response(('click_text', '{"text":"Option initial"}'))]
    for i in range(3):
        calls += [response(('get_page_elements', '{}')), response(('click_text', json.dumps({'text': f'Option {i}'})))]
    model(monkeypatch, *calls)
    result = agent.run_agent('Open menu')
    assert not result['success'] and not result['outcome_verified']
    assert click.call_count == 3
    assert 'could not be verified' in result['message']


def test_total_tool_limit_counts_calls_and_rejects_batches(monkeypatch):
    read = Mock(return_value={'success': True})
    monkeypatch.setitem(agent.TOOLS, 'read_page', read)
    model(monkeypatch, *[response(('read_page', '{}')) for _ in range(13)])
    result = agent.run_agent('Read')
    assert not result['success'] and read.call_count == 12
    read.reset_mock()
    model(monkeypatch, response(('read_page', '{}'), ('read_page', '{}')))
    assert not agent.run_agent('Read')['success']
    read.assert_not_called()


def test_delayed_observation_can_verify_without_duplicate_action(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(return_value=dispatched('home', 'home')))
    fresh = observation('fresh', 'Settings')
    fresh['state'] = {'url': 'settings', 'dialogs': []}
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(return_value=fresh))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')),
          response(('get_page_elements', '{}')), verified_url('settings'), response())
    assert agent.run_agent('Open Settings')['success']
    agent.TOOLS['click_text'].assert_called_once()


def test_failed_action_cannot_be_verified_with_real_looking_evidence(monkeypatch):
    failure = dispatched('home', 'settings')
    failure['success'] = False
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(return_value=failure))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')), verified_url('settings'), response(text='Done'))
    assert not agent.run_agent('Open Settings')['success']


def test_recovery_rejects_superseded_observation_id(monkeypatch):
    click = Mock(return_value={'success': False})
    monkeypatch.setitem(agent.TOOLS, 'click_element', click)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(side_effect=[observation('old', 'Settings'), observation('fresh', 'Open settings')]))
    model(monkeypatch, response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"old","element_id":"e0"}')),
          response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"old","element_id":"e0"}')), response(text='Done'))
    result = agent.run_agent('Open Settings')
    assert not result['success'] and click.call_count == 1
    assert 'latest observation' in result['actions'][-1]['result']['message']


def test_failed_reobservation_does_not_authorize_recovery(monkeypatch):
    click = Mock(return_value={'success': False})
    monkeypatch.setitem(agent.TOOLS, 'click_text', click)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(side_effect=[observation('fresh', 'Alternative'), {'success': False}]))
    model(monkeypatch, response(('click_text', '{"text":"Settings"}')),
          response(('get_page_elements', '{}')), response(('get_page_elements', '{}')),
          response(('click_text', '{"text":"Alternative"}')), response())
    result = agent.run_agent('Open Settings')
    assert not result['success'] and click.call_count == 1
    assert 'fresh' in result['actions'][-1]['result']['message']


def test_coordinate_recovery_cannot_repeat_same_visual_target(monkeypatch):
    visual = lambda oid, x: {**observation(oid), 'analysis': {'elements': [
        {'description': 'Settings icon', 'x': x, 'y': 20}]}}
    click = Mock(return_value={'success': False})
    monkeypatch.setitem(agent.TOOLS, 'click_coordinates', click)
    monkeypatch.setitem(agent.TOOLS, 'analyze_current_screen', Mock(side_effect=[visual('one', 20), visual('two', 21)]))
    model(monkeypatch, response(('analyze_current_screen', '{}')),
          response(('click_coordinates', '{"observation_id":"one","x":20,"y":20}')),
          response(('analyze_current_screen', '{}')),
          response(('click_coordinates', '{"observation_id":"two","x":21,"y":20}')), response())
    result = agent.run_agent('Open Settings')
    assert not result['success'] and click.call_count == 1
    assert 'already attempted' in result['actions'][-1]['result']['message']


def infrastructure_failure():
    return {'success': False, 'error': 'browser_unavailable', 'failure_kind': 'infrastructure',
            'action_executed': False, 'message': 'Target page, context or browser has been closed'}


def test_infrastructure_failure_allows_same_navigation_without_semantic_retry(monkeypatch):
    opened = dispatched('about:blank', 'https://www.youtube.com/', url='https://www.youtube.com/')
    navigate = Mock(side_effect=[infrastructure_failure(), opened])
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', navigate)
    create = model(monkeypatch, response(('open_browser_url', '{"url":"https://www.youtube.com/"}')),
                   response(('open_browser_url', '{"url":"https://www.youtube.com/"}')),
                   verified_url('https://www.youtube.com/'), response())
    result = agent.run_agent('Open YouTube')
    assert result['success'] and result['outcome_verified']
    assert navigate.call_count == 2
    assert all(a['result'].get('error') != 'recovery_guard' for a in result['actions'])
    assert json.loads(create.call_args_list[1].kwargs['input'][0]['output'])['failure_kind'] == 'infrastructure'


def test_infrastructure_failure_does_not_disappear_after_successful_blank_observation(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', Mock(return_value=infrastructure_failure()))
    blank = {**observation('blank'), 'state': {'url': 'about:blank', 'dialogs': []}}
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(return_value=blank))
    model(monkeypatch, response(('open_browser_url', '{"url":"https://www.youtube.com/"}')),
          response(('get_page_elements', '{}')), response(text='Done'))
    result = agent.run_agent('Open YouTube')
    assert not result['success'] and not result['outcome_verified']
    assert 'Chromium was unavailable' in result['message']


def test_infrastructure_failure_budget_stops_repeated_unavailable_navigation(monkeypatch):
    navigate = Mock(return_value=infrastructure_failure())
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', navigate)
    model(monkeypatch, response(('open_browser_url', '{"url":"https://www.youtube.com/"}')),
          response(('open_browser_url', '{"url":"https://www.youtube.com/"}')))
    result = agent.run_agent('Open YouTube')
    assert not result['success'] and navigate.call_count == 2
    assert len(result['actions']) == 2


def test_stale_rejection_requires_fresh_observation_but_does_not_poison_target(monkeypatch):
    click = Mock(side_effect=[{'success': False, 'error': 'stale_observation', 'action_executed': False}, dispatched('home', 'settings')])
    monkeypatch.setitem(agent.TOOLS, 'click_element', click)
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(side_effect=[observation('old', 'Settings'), observation('fresh', 'Settings')]))
    model(monkeypatch, response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"old","element_id":"e0"}')),
          response(('click_element', '{"observation_id":"old","element_id":"e0"}')),
          response(('get_page_elements', '{}')),
          response(('click_element', '{"observation_id":"fresh","element_id":"e0"}')),
          verified_url('settings'), response())
    result = agent.run_agent('Open Settings')
    assert result['success'] and click.call_count == 2
    assert result['actions'][2]['result']['error'] == 'recovery_guard'
    assert [c.kwargs['observation_id'] for c in click.call_args_list] == ['old', 'fresh']


def test_blank_observation_cannot_complete_successful_navigation_prerequisite(monkeypatch):
    opened = dispatched('about:blank', 'https://www.youtube.com/', url='https://www.youtube.com/')
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', Mock(return_value=opened))
    blank = {**observation('blank'), 'state': {'url': 'about:blank', 'dialogs': []}}
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(return_value=blank))
    type_into = Mock()
    monkeypatch.setitem(agent.TOOLS, 'type_text', type_into)
    model(monkeypatch, response(('open_browser_url', '{"url":"https://www.youtube.com/"}')),
          response(('get_page_elements', '{}')),
          response(('type_text', '{"field":"Search","text":"Nexa verification test"}')), response(text='Done'))
    result = agent.run_agent('Open YouTube and type Nexa verification test')
    assert not result['success']
    type_into.assert_not_called()


def test_navigation_can_still_be_explicitly_verified_after_fresh_observation(monkeypatch):
    url = 'https://www.youtube.com/'
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', Mock(return_value=dispatched('about:blank', url, url=url)))
    observed = {**observation('fresh'), 'state': {'url': url, 'dialogs': []}}
    monkeypatch.setitem(agent.TOOLS, 'get_page_elements', Mock(return_value=observed))
    model(monkeypatch, response(('open_browser_url', json.dumps({'url': url}))),
          response(('get_page_elements', '{}')), verified_url(url), response())
    result = agent.run_agent('Open YouTube')
    assert result['success'] and result['outcome_verified']
    assert result['actions'][-1]['result']['success']
