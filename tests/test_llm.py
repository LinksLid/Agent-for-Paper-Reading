from dataclasses import replace
import json

import httpx
import pytest

from paperteam.config import Settings
from paperteam.llm import LLM
from paperteam.models import Analysis


def client(tmp_path, handler, max_calls=3):
    llm = LLM(Settings(root=tmp_path, base_url="https://mock.invalid/v1", api_key="test-secret", model="test-model", max_calls=max_calls), tmp_path)
    llm.http.close()
    llm.http = httpx.Client(transport=httpx.MockTransport(handler))
    return llm


def test_aliases_load_existing_env_without_modification(tmp_path, monkeypatch):
    for key in ("OPENAI_BASE_URL", "BASE_URL", "OPENAI_API_KEY", "API_KEY", "MODEL", "OPENAI_MODEL", "Base_url"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    content = "Base_url=https://api.example.test/v1\nAPI_KEY=example-secret\n"
    path.write_text(content, encoding="utf-8")
    settings = Settings.load(tmp_path)
    assert settings.base_url == "https://api.example.test/v1"
    assert settings.api_key == "example-secret"
    assert path.read_text(encoding="utf-8") == content


def test_api_budget_and_no_secret_in_trace(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 7}})
    llm = client(tmp_path, handler, max_calls=1)
    assert llm.chat("test", "hello") == "ok"
    with pytest.raises(RuntimeError, match="调用上限"):
        llm.chat("test", "hello")
    log = (tmp_path / "api_usage.jsonl").read_text(encoding="utf-8")
    assert "test-secret" not in log and "hello" not in log
    llm.close()


def test_invalid_json_gets_one_format_retry(tmp_path):
    responses = iter(["not json", '{"claims": []}'])
    llm = client(tmp_path, lambda _: httpx.Response(200, json={"choices": [{"message": {"content": next(responses)}}]}))
    assert llm.structured("test", "hello", Analysis).claims == []
    assert llm.calls == 2
    llm.close()


def test_provider_error_does_not_echo_body_or_key(tmp_path):
    llm = client(tmp_path, lambda _: httpx.Response(403, text="secret test-secret confidential response"))
    with pytest.raises(RuntimeError) as failure:
        llm.chat("test", "hello")
    assert "test-secret" not in str(failure.value) and "confidential" not in str(failure.value)
    llm.close()
