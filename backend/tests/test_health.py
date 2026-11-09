from concurrent.futures import ThreadPoolExecutor
import threading
import time
from fastapi.testclient import TestClient
from app import main


def test_health_without_openai_initialization(monkeypatch):
    def unexpected_client():
        raise AssertionError('Health must not initialize OpenAI')
    monkeypatch.setattr('app.ai.get_client', unexpected_client)
    with TestClient(main.app) as client:
        assert client.get('/health').json() == {'status': 'healthy'}
        assert client.get('/').json()['name'] == 'Nexa'
        assert client.get('/debug/screen-dimensions').status_code == 404


def test_agent_requests_share_one_worker_and_do_not_interleave(monkeypatch):
    events = []
    def run(message):
        events.append(('start', message, threading.get_ident()))
        time.sleep(0.02)
        events.append(('end', message, threading.get_ident()))
        return {'success': True}
    monkeypatch.setattr(main, 'run_agent', run)
    with TestClient(main.app) as client, ThreadPoolExecutor(max_workers=2) as callers:
        requests = [callers.submit(client.post, '/agent', params={'message': str(i)}) for i in range(2)]
        assert all(request.result().json()['success'] for request in requests)
    assert [event[0] for event in events] == ['start', 'end', 'start', 'end']
    assert len({event[2] for event in events}) == 1


def test_agent_browser_timeout_returns_structured_result_not_http_500(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from playwright.sync_api import TimeoutError
    from app import agent
    tool_call = SimpleNamespace(type='function_call', name='click_text',
                                arguments='{"text":"missing"}', call_id='click')
    responses = [SimpleNamespace(id='one', output=[tool_call], output_text=''),
                 SimpleNamespace(id='two', output=[], output_text='Could not locate target.')]
    monkeypatch.setattr(agent, 'get_client', lambda: SimpleNamespace(
        responses=SimpleNamespace(create=Mock(side_effect=responses))))
    monkeypatch.setitem(agent.TOOLS, 'click_text', Mock(side_effect=TimeoutError('Target not found')))
    with TestClient(main.app) as client:
        result = client.post('/agent', params={'message': 'Click missing'})
    assert result.status_code == 200
    assert result.json()['success'] is False
    assert result.json()['actions'][0]['result']['success'] is False
