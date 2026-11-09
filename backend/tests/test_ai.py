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
