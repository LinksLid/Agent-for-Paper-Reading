import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from paperteam.repositories import repo_name, unpack_snapshot, inspect_repository
from paperteam.experiment_runtime import safe_relative, patched_text, Broker
from paperteam.repository_experiments import (ExperimentPlan, ExperimentCommand, extract_metrics,
                                               validate_plan, summarize, build_repository_workflow)


def test_repository_links_exclude_dependency_site_pages():
    assert repo_name("https://github.com/owner/repo/tree/main") == "owner/repo"
    assert repo_name("https://github.com/owner/repo.git") == "owner/repo"
    assert repo_name("http://127.0.0.1/owner/repo") is None
    assert repo_name("https://github.com/search?q=paper") is None


@pytest.mark.parametrize("member", ["root/../escape.py", "/root/escape.py", "root/dir\\..\\..\\escape.py", "root/.git/config"])
def test_archive_rejects_unsafe_members_before_writing(tmp_path, member):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("root/safe.py", "safe")
        archive.writestr(member, "bad")
    with pytest.raises(ValueError):
        unpack_snapshot(stream.getvalue(), tmp_path / "repo")
    assert not (tmp_path / "repo/safe.py").exists()


def test_notebook_inspection_includes_actual_experiment_cells(tmp_path):
    (tmp_path / "README.md").write_text("Run hotpotqa.ipynb", encoding="utf-8")
    notebook = {"cells": [{"cell_type": "code", "source": ["for i in idxs[:500]:\n", "    run(i)\n"]}]}
    (tmp_path / "hotpotqa.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    result = inspect_repository(tmp_path)
    assert "for i in idxs[:500]:" in result["contents"]["hotpotqa.ipynb"]


def test_notebook_compatibility_patches_keep_prior_changes():
    original = json.dumps({"cells": [{"cell_type": "code", "source": ["limit = 500\n", "print(limit)\n"]}]})
    first = patched_text(original, ".ipynb", "limit = 500", "limit = 2")
    final = patched_text(first, ".ipynb", "print(limit)", "save_metric(limit)")
    text = "".join(json.loads(final)["cells"][0]["source"])
    assert "limit = 2" in text and "save_metric(limit)" in text


@pytest.mark.parametrize("value", ["../key", "C:/Users/key", "/etc/passwd", ".env", "dir/.env", "dir\\file"])
def test_command_paths_stay_in_repo(value):
    with pytest.raises(ValueError):
        safe_relative(value)


def test_metrics_require_an_existing_numeric_artifact(tmp_path):
    p = tmp_path / "artifacts/result.json"
    p.parent.mkdir()
    p.write_text('{"scores":{"em":0.5},"claimed":"100%","invalid":NaN}', encoding="utf-8")
    specs = [{"name": "EM", "artifact": "result.json", "json_path": "scores.em"},
             {"name": "fake", "artifact": "result.json", "json_path": "claimed"},
             {"name": "NaN", "artifact": "result.json", "json_path": "invalid"},
             {"name": "missing", "artifact": "absent.json", "json_path": "score"}]
    assert extract_metrics(tmp_path, specs, ["artifacts/result.json"]) == [{**specs[0], "value": .5, "provenance": "artifacts/result.json"}]


def test_incomplete_plan_is_not_executable():
    plan = ExperimentPlan(runnable=True, scope="benchmark_subset", reason="test", data_source="missing", missing_requirements=["data"],
                          uses_llm=False, install=[], run=[], artifacts=[], differences_from_paper=[])
    with pytest.raises(ValueError):
        validate_plan(plan, {"files": [], "contents": {}})


def test_failed_runs_not_reported_as_success_or_unavailable():
    result = summarize([{"status": "failed", "commit": "test"}], 0)
    assert result["status"] == "failed" and result["completed"] == 0


def test_missing_repository_does_not_execute(tmp_path):
    class Github:
        def discover(self, *args):
            return None, {"searches": [], "reason": "No matching implementation"}
    graph = build_repository_workflow(object(), Github(), tmp_path)
    result = graph.invoke({"paper": {"filename": "paper.pdf"}, "events": []})
    assert result["status"] == "not_found" and result["stage"] == "search"


def test_broker_keeps_legacy_prompt_and_stop_options():
    import httpx
    class Client:
        records = [{"model": "test", "usage": {"total_tokens": 12}}]
        def chat(self, role, prompt, **kwargs):
            self.kwargs = kwargs
            return "Finish[answer]"
    client = Client()
    with Broker(client, max_calls=1) as broker:
        with httpx.Client(trust_env=False) as http:
            url = f"http://127.0.0.1:{broker.port}/v1/completions"
            headers = {"Authorization": "Bearer " + broker.token}
            response = http.post(url, headers=headers, json={"prompt": "Question: actual", "stop": ["Observation"]})
            assert response.status_code == 200 and response.json()["choices"][0]["text"] == "Finish[answer]"
            assert client.kwargs["messages"] == [{"role": "user", "content": "Question: actual"}]
            assert client.kwargs["request_options"]["stop"] == ["Observation"]
            assert http.post(url, headers=headers, json={"prompt": "second"}).status_code == 429


def test_completion_continuation_is_explicit_and_does_not_change_chat_requests():
    import httpx
    class Client:
        records = [{"model": "test", "usage": {}}]
        def chat(self, role, prompt, **kwargs):
            self.messages = kwargs["messages"]
            return "go to desk 1"
    client = Client()
    with Broker(client, max_calls=2, completion_continuation=True) as broker:
        with httpx.Client(trust_env=False) as http:
            url = f"http://127.0.0.1:{broker.port}/v1"
            headers = {"Authorization": "Bearer " + broker.token}
            prompt = "Official examples and current observation\n>"
            result = http.post(url + "/completions", headers=headers, json={"prompt": prompt, "stop": ["\n"]})
            assert result.status_code == 200
            assert client.messages[0]["role"] == "system" and "text completion engine" in client.messages[0]["content"]
            assert client.messages[1] == {"role": "user", "content": prompt}
            messages = [{"role": "user", "content": "Original chat prompt"}]
            assert http.post(url + "/chat/completions", headers=headers, json={"messages": messages}).status_code == 200
            assert client.messages == messages
