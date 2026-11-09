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


def test_multistep_preserves_sequential_tool_calls(monkeypatch):
    search, first = Mock(return_value={'success': True}), Mock(return_value={'success': True})
    monkeypatch.setitem(agent.TOOLS, 'search_youtube', search)
    monkeypatch.setitem(agent.TOOLS, 'open_first_youtube_result', first)
    create = model(monkeypatch, response(('search_youtube', '{"query":"Python tutorials"}')),
                   response(('open_first_youtube_result', '{}')), response())
    result = agent.run_agent('Search YouTube for Python tutorials and open the first result')
    assert result['success']
    assert [a['tool'] for a in result['actions']] == ['search_youtube', 'open_first_youtube_result']
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
    controlled = Mock(return_value={'success': True, 'browser': 'nexa'})
    default = Mock(return_value={'success': True, 'browser': 'default'})
    monkeypatch.setitem(agent.TOOLS, 'open_browser_url', controlled)
    monkeypatch.setitem(agent.TOOLS, 'open_website', default)
    create = model(monkeypatch, response((tool_name, '{"url":"https://www.youtube.com"}')), response())
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
