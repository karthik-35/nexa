import struct
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app import ai


def test_client_loads_backend_env_lazily(monkeypatch):
    ai.get_client.cache_clear()
    load, construct = Mock(), Mock()
    monkeypatch.setattr(ai, 'load_dotenv', load)
    monkeypatch.setattr(ai, 'OpenAI', construct)
    ai.get_client()
    ai.get_client()
    assert load.call_args.args[0] == ai.Path(ai.__file__).resolve().parents[1] / '.env'
    assert construct.call_count == 1
    ai.get_client.cache_clear()


def test_vision_schema_and_original_dimensions(tmp_path, monkeypatch):
    path = tmp_path / 'screen.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 1280, 720))
    analysis = ai.ScreenAnalysis(page='Test', summary='A control', elements=[])
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=analysis))
    monkeypatch.setattr(ai, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    assert ai.analyze_screenshot(str(path))['summary'] == 'A control'
    request = parse.call_args.kwargs
    assert request['text_format'] is ai.ScreenAnalysis
    assert request['model'] == 'gpt-4.1-mini'
    content = request['input'][0]['content']
    assert '1280 by 720' in content[0]['text']
    assert content[1]['detail'] == 'high'
    parse.return_value.output_parsed = None
    with pytest.raises(ValueError, match='No click is authorized'):
        ai.analyze_screenshot(str(path))


def test_invalid_png_rejected():
    with pytest.raises(ValueError):
        ai.png_dimensions(b'not a png')


def test_task_plan_extracts_ordered_intent_without_executable_targets():
    plan = ai.TaskPlan(steps=[ai.TaskStep(kind='navigate', description='Open site', value='example.com'),
                              ai.TaskStep(kind='type', description='Enter exact query', value='FastAPI tutorial'),
                              ai.TaskStep(kind='submit', description='Search', value='FastAPI tutorial'),
                              ai.TaskStep(kind='activate', description='Open first result', value=None),
                              ai.TaskStep(kind='back', description='Return to results', value=None)])
    parse = Mock(return_value=SimpleNamespace(status='completed', output_parsed=plan))
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    message = 'Open example.com, search FastAPI tutorial, open first result, go back'
    assert ai.plan_task(client, message) is plan
    request = parse.call_args.kwargs
    assert request['input'] == message and request['text_format'] is ai.TaskPlan
    assert plan.steps[0].value == 'https://example.com/'
    assert set(ai.TaskStep.model_json_schema()['properties']) == {'kind', 'description', 'value'}
    parse.return_value.output_parsed = None
    with pytest.raises(ValueError, match='no actions were executed'):
        ai.plan_task(client, message)


@pytest.mark.parametrize('steps', [
    [{'kind': 'type', 'description': 'Type', 'value': None}],
    [{'kind': 'navigate', 'description': 'Open', 'value': 'javascript:alert(1)'}],
    [{'kind': 'shell', 'description': 'Run', 'value': 'anything'}],
    [{'kind': 'back', 'description': 'Back', 'value': None}] * 11,
])
def test_invalid_or_unbounded_task_plans_are_rejected(steps):
    with pytest.raises(ValueError):
        ai.TaskPlan(steps=steps)
