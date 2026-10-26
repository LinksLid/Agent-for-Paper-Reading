from dataclasses import replace
import json

import httpx
import pytest

from paperteam.budget import Budget, BudgetExhausted
from paperteam.config import Settings
from paperteam.llm import LLM


def test_budget_is_shared_between_instances_and_refunds_unused_reservation(tmp_path):
    settings = Settings(root=tmp_path, api_key="fixture-key", deepseek_budget_cny=.02)
    first = Budget(settings)
    reservation = first.reserve(25, "deepseek-flash", 1000, 100)
    second = Budget(settings)
    with pytest.raises(BudgetExhausted):
        second.reserve(25, "deepseek-flash", 1000, 100)
    first.settle(reservation, {"prompt_tokens": 10, "completion_tokens": 1}, "deepseek-flash")
    assert second.reserve(25, "deepseek-flash", 1000, 100) > 0


def test_account_balance_drop_also_stops_budget(tmp_path):
    budget = Budget(Settings(root=tmp_path, api_key="fixture-key", deepseek_budget_cny=20))
    reservation = budget.reserve(25, "deepseek-flash", 10, 100)
    budget.settle(reservation, {"prompt_tokens": 10, "completion_tokens": 1}, "deepseek-flash")
    with pytest.raises(BudgetExhausted):
        budget.reserve(4.99, "deepseek-flash", 10, 100)


def test_http_402_switches_and_retries_same_prompt_without_restart(tmp_path, monkeypatch):
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append((request.url.host, request.url.path))
        if request.url.path.endswith("/user/balance"):
            return httpx.Response(200, json={"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "25"}]})
        if request.url.host == "primary.invalid":
            return httpx.Response(402, json={"error": "insufficient balance"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "continued"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    fallback = Settings(root=tmp_path, base_url="https://fallback.invalid/v1", api_key="fallback-secret", model="school-test", provider="tongji")
    primary = Settings(root=tmp_path, base_url="https://primary.invalid/v1", api_key="primary-secret", model="deepseek-flash",
                       provider="deepseek", fallback=fallback)
    llm = LLM(primary, tmp_path / "logs")
    assert llm.chat("test", "same prompt") == "continued"
    assert llm.calls == 2 and llm.settings.provider == "tongji"
    assert len(llm.switches) == 1
    assert llm.budget.summary()["exhausted"]
    assert requests[-1] == ("fallback.invalid", "/v1/chat/completions")
    llm.close()


def test_low_balance_switches_before_paid_request(tmp_path, monkeypatch):
    requests = []
    real_client = httpx.Client

    def handler(request):
        requests.append((request.url.host, request.url.path))
        if request.url.path.endswith("/user/balance"):
            return httpx.Response(200, json={"is_available": False, "balance_infos": [{"currency": "CNY", "total_balance": "0"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "fallback"}}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    fallback = Settings(root=tmp_path, base_url="https://fallback.invalid", api_key="test", model="school-test")
    primary = Settings(root=tmp_path, base_url="https://primary.invalid", api_key="test", model="deepseek-flash",
                       provider="deepseek", fallback=fallback)
    llm = LLM(primary)
    assert llm.chat("test", "hello") == "fallback"
    assert not any(host == "primary.invalid" and path.endswith("/chat/completions") for host, path in requests)
    llm.close()
